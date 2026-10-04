-- Bind immutable PAPER reconciliation receipts to the durable lifecycle
-- envelope they claim to acknowledge. Effect membership remains verified by the
-- reconciliation repository; this guard prevents malformed or cross-job receipt
-- metadata from permanently occupying the unique audit identity.
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
        RAISE EXCEPTION 'PAPER_RECONCILIATION_AUDIT_PAYLOAD_INVALID'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION guard_paper_reconciliation_audit_payload()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.event_type = 'PAPER_RUN_RECONCILED'
       AND NOT paper_reconciliation_audit_payload_matches_job(
           NEW.entity_id,
           NEW.payload
       )
    THEN
        RAISE EXCEPTION 'PAPER_RECONCILIATION_AUDIT_PAYLOAD_INVALID'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_paper_reconciliation_audit_zz_payload ON audit_events;
CREATE TRIGGER trg_paper_reconciliation_audit_zz_payload
BEFORE INSERT ON audit_events
FOR EACH ROW
EXECUTE FUNCTION guard_paper_reconciliation_audit_payload();
