-- Require reconciliation receipts to authenticate one complete terminal PAPER effect shape.
-- Exact ledger membership is necessary but insufficient: a partial ledger must never
-- become durable proof that an interrupted run completed without replay.
CREATE OR REPLACE FUNCTION paper_reconciliation_audit_payload_matches_job(
    receipt_entity_id TEXT,
    receipt_payload JSONB
)
RETURNS BOOLEAN
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    durable_job_key TEXT;
    durable_scheduled_for TIMESTAMPTZ;
    durable_completed_at TIMESTAMPTZ;
    payload_key_count BIGINT;
    durable_effect_count BIGINT;
    durable_effect_types TEXT[];
BEGIN
    IF receipt_entity_id !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
       OR jsonb_typeof(receipt_payload) IS DISTINCT FROM 'object'
    THEN
        RETURN FALSE;
    END IF;

    SELECT job_key, scheduled_for, completed_at
      INTO durable_job_key, durable_scheduled_for, durable_completed_at
      FROM job_runs
     WHERE job_run_id = receipt_entity_id::uuid;

    IF NOT FOUND OR durable_completed_at IS NULL THEN
        RETURN FALSE;
    END IF;

    SELECT count(*)
      INTO payload_key_count
      FROM jsonb_object_keys(receipt_payload);

    IF payload_key_count <> 6
       OR receipt_payload -> 'schema_version' IS DISTINCT FROM '1'::jsonb
       OR receipt_payload ->> 'decision' IS DISTINCT FROM 'ACKNOWLEDGE_COMPLETE_EFFECTS'
       OR receipt_payload ->> 'job_key' IS DISTINCT FROM durable_job_key
       OR jsonb_typeof(receipt_payload -> 'effects') IS DISTINCT FROM 'array'
    THEN
        RETURN FALSE;
    END IF;

    BEGIN
        IF (receipt_payload ->> 'scheduled_for')::timestamptz
               IS DISTINCT FROM durable_scheduled_for
           OR (receipt_payload ->> 'completed_at')::timestamptz
               IS DISTINCT FROM durable_completed_at
        THEN
            RETURN FALSE;
        END IF;
    EXCEPTION
        WHEN invalid_datetime_format OR datetime_field_overflow THEN
            RETURN FALSE;
    END;

    SELECT count(*), array_agg(effect_type ORDER BY effect_type)
      INTO durable_effect_count, durable_effect_types
      FROM paper_effects
     WHERE job_run_id = receipt_entity_id::uuid;

    IF durable_effect_types IS DISTINCT FROM
           ARRAY['FILL', 'ORDER', 'PNL', 'RISK', 'SIGNAL']::TEXT[]
       AND durable_effect_types IS DISTINCT FROM
           ARRAY['CANCELLATION', 'ORDER', 'RISK', 'SIGNAL']::TEXT[]
       AND durable_effect_types IS DISTINCT FROM
           ARRAY['ORDER', 'REJECTION', 'RISK', 'SIGNAL']::TEXT[]
    THEN
        RETURN FALSE;
    END IF;

    IF jsonb_array_length(receipt_payload -> 'effects') <> durable_effect_count
    THEN
        RETURN FALSE;
    END IF;

    IF EXISTS (
        SELECT 1
          FROM jsonb_array_elements(receipt_payload -> 'effects')
               AS receipt_effect(value)
         WHERE jsonb_typeof(receipt_effect.value) IS DISTINCT FROM 'object'
            OR NOT EXISTS (
                SELECT 1
                  FROM paper_effects durable_effect
                 WHERE durable_effect.job_run_id = receipt_entity_id::uuid
                   AND receipt_effect.value = jsonb_build_object(
                       'effect_id', durable_effect.effect_id::text,
                       'effect_type', durable_effect.effect_type,
                       'entity_id', durable_effect.entity_id::text,
                       'payload_hash', durable_effect.payload_hash
                   )
            )
    ) THEN
        RETURN FALSE;
    END IF;

    IF EXISTS (
        SELECT 1
          FROM paper_effects durable_effect
         WHERE durable_effect.job_run_id = receipt_entity_id::uuid
           AND NOT EXISTS (
               SELECT 1
                 FROM jsonb_array_elements(receipt_payload -> 'effects')
                      AS receipt_effect(value)
                WHERE receipt_effect.value = jsonb_build_object(
                    'effect_id', durable_effect.effect_id::text,
                    'effect_type', durable_effect.effect_type,
                    'entity_id', durable_effect.entity_id::text,
                    'payload_hash', durable_effect.payload_hash
                )
           )
    ) THEN
        RETURN FALSE;
    END IF;

    RETURN TRUE;
END;
$$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM audit_events
         WHERE event_type = 'PAPER_RUN_RECONCILED'
           AND NOT paper_reconciliation_audit_payload_matches_job(entity_id, payload)
    ) THEN
        RAISE EXCEPTION 'PAPER_RECONCILIATION_AUDIT_EFFECT_SHAPE_INVALID'
            USING ERRCODE = '23514';
    END IF;
END;
$$;
