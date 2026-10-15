-- Persist explicit zero-signal strategy outcomes so a crash after the decision
-- but before job completion can be reconciled without replaying the strategy.
ALTER TABLE paper_effects DROP CONSTRAINT IF EXISTS paper_effects_effect_type_check;
ALTER TABLE paper_effects ADD CONSTRAINT paper_effects_effect_type_check
    CHECK (effect_type IN (
        'NO_TRADE','SIGNAL','RISK','ORDER','FILL','PNL','CANCELLATION','REJECTION'
    ));

CREATE TABLE paper_no_trade_decisions (
    decision_id UUID PRIMARY KEY,
    job_run_id UUID NOT NULL UNIQUE REFERENCES job_runs(job_run_id),
    strategy_version TEXT NOT NULL CHECK (
        strategy_version <> '' AND strategy_version = btrim(strategy_version)
    ),
    decision_time TIMESTAMPTZ NOT NULL,
    inputs_hash CHAR(64) NOT NULL CHECK (inputs_hash ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
    CONSTRAINT paper_no_trade_decision_job_identity
        CHECK (decision_id = job_run_id)
);

CREATE OR REPLACE FUNCTION hope_paper_no_trade_payload_hash(
    decision_id UUID,
    strategy_version TEXT,
    decision_time TIMESTAMPTZ,
    inputs_hash TEXT
)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
STRICT
AS $$
    SELECT encode(
        digest(
            convert_to(
                decision_id::TEXT || '|' ||
                strategy_version || '|' ||
                to_char(
                    decision_time AT TIME ZONE 'UTC',
                    'YYYY-MM-DD"T"HH24:MI:SS'
                ) ||
                CASE
                    WHEN (extract(microseconds FROM decision_time)::INTEGER %% 1000000) = 0
                    THEN ''
                    ELSE '.' || to_char(decision_time AT TIME ZONE 'UTC', 'US')
                END ||
                '+00:00|' || inputs_hash,
                'UTF8'
            ),
            'sha256'
        ),
        'hex'
    );
$$;

CREATE OR REPLACE FUNCTION hope_guard_paper_no_trade_decision()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    owner_status TEXT;
    owner_schedule TIMESTAMPTZ;
    durable_hash TEXT;
BEGIN
    SELECT status, scheduled_for
      INTO owner_status, owner_schedule
      FROM job_runs
     WHERE job_run_id = NEW.job_run_id
     FOR UPDATE;
    IF owner_status IS DISTINCT FROM 'CLAIMED' THEN
        RAISE EXCEPTION 'PAPER_NO_TRADE_REQUIRES_CLAIMED_JOB'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.decision_id IS DISTINCT FROM NEW.job_run_id THEN
        RAISE EXCEPTION 'PAPER_NO_TRADE_JOB_IDENTITY_MISMATCH'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.decision_time IS DISTINCT FROM owner_schedule THEN
        RAISE EXCEPTION 'PAPER_NO_TRADE_DECISION_TIME_MISMATCH'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.created_at IS DISTINCT FROM transaction_timestamp() THEN
        RAISE EXCEPTION 'PAPER_NO_TRADE_TIMESTAMP_NOT_DATABASE_AUTHENTICATED'
            USING ERRCODE = '23514';
    END IF;
    SELECT payload_hash INTO durable_hash
      FROM paper_effects
     WHERE effect_type = 'NO_TRADE' AND entity_id = NEW.decision_id;
    IF FOUND AND durable_hash IS DISTINCT FROM hope_paper_no_trade_payload_hash(
        NEW.decision_id,
        NEW.strategy_version,
        NEW.decision_time,
        NEW.inputs_hash
    ) THEN
        RAISE EXCEPTION 'PAPER_NO_TRADE_EFFECT_PAYLOAD_MISMATCH'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_no_trade_decision_guard
BEFORE INSERT ON paper_no_trade_decisions
FOR EACH ROW EXECUTE FUNCTION hope_guard_paper_no_trade_decision();

CREATE OR REPLACE FUNCTION hope_reject_paper_no_trade_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'PAPER_NO_TRADE_IMMUTABLE'
        USING ERRCODE = '23514';
END;
$$;

CREATE TRIGGER trg_paper_no_trade_immutable
BEFORE UPDATE OR DELETE ON paper_no_trade_decisions
FOR EACH ROW EXECUTE FUNCTION hope_reject_paper_no_trade_mutation();

CREATE OR REPLACE FUNCTION hope_require_paper_no_trade_pair_from_decision()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM paper_effects effect
         WHERE effect.effect_type = 'NO_TRADE'
           AND effect.entity_id = NEW.decision_id
           AND effect.job_run_id = NEW.job_run_id
           AND effect.payload_hash = hope_paper_no_trade_payload_hash(
               NEW.decision_id,
               NEW.strategy_version,
               NEW.decision_time,
               NEW.inputs_hash
           )
    ) THEN
        RAISE EXCEPTION 'PAPER_NO_TRADE_EFFECT_REQUIRED'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_paper_no_trade_effect_required
AFTER INSERT ON paper_no_trade_decisions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION hope_require_paper_no_trade_pair_from_decision();

CREATE OR REPLACE FUNCTION hope_require_paper_no_trade_pair_from_effect()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.effect_type <> 'NO_TRADE' THEN
        RETURN NULL;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM paper_no_trade_decisions decision
         WHERE decision.decision_id = NEW.entity_id
           AND decision.job_run_id = NEW.job_run_id
           AND hope_paper_no_trade_payload_hash(
               decision.decision_id,
               decision.strategy_version,
               decision.decision_time,
               decision.inputs_hash
           ) = NEW.payload_hash
    ) THEN
        RAISE EXCEPTION 'PAPER_NO_TRADE_DECISION_REQUIRED'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_paper_no_trade_decision_required
AFTER INSERT ON paper_effects
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION hope_require_paper_no_trade_pair_from_effect();

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
    durable_effects JSONB;
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

    SELECT count(*) INTO payload_key_count FROM jsonb_object_keys(receipt_payload);
    IF payload_key_count <> 6
       OR receipt_payload -> 'schema_version' IS DISTINCT FROM '1'::jsonb
       OR receipt_payload ->> 'decision' IS DISTINCT FROM 'ACKNOWLEDGE_COMPLETE_EFFECTS'
       OR receipt_payload ->> 'job_key' IS DISTINCT FROM durable_job_key
       OR jsonb_typeof(receipt_payload -> 'effects') IS DISTINCT FROM 'array'
    THEN
        RETURN FALSE;
    END IF;

    BEGIN
        IF (receipt_payload ->> 'scheduled_for')::timestamptz IS DISTINCT FROM durable_scheduled_for
           OR (receipt_payload ->> 'completed_at')::timestamptz IS DISTINCT FROM durable_completed_at
        THEN
            RETURN FALSE;
        END IF;
    EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN
        RETURN FALSE;
    END;

    SELECT count(*), array_agg(effect_type ORDER BY effect_type),
           jsonb_agg(jsonb_build_object(
               'effect_id', effect_id::text,
               'effect_type', effect_type,
               'entity_id', entity_id::text,
               'payload_hash', payload_hash
           ) ORDER BY created_at, effect_id)
      INTO durable_effect_count, durable_effect_types, durable_effects
      FROM paper_effects
     WHERE job_run_id = receipt_entity_id::uuid;

    IF durable_effect_types IS DISTINCT FROM ARRAY['NO_TRADE']::TEXT[]
       AND NOT (durable_effect_count > 0 AND durable_effect_types <@ ARRAY['SIGNAL']::TEXT[])
       AND durable_effect_types IS DISTINCT FROM ARRAY['ORDER', 'RISK']::TEXT[]
       AND durable_effect_types IS DISTINCT FROM ARRAY['FILL', 'PNL']::TEXT[]
       AND durable_effect_types IS DISTINCT FROM ARRAY['FILL', 'ORDER', 'PNL', 'RISK', 'SIGNAL']::TEXT[]
       AND durable_effect_types IS DISTINCT FROM ARRAY['CANCELLATION', 'FILL', 'ORDER', 'PNL', 'RISK', 'SIGNAL']::TEXT[]
       AND durable_effect_types IS DISTINCT FROM ARRAY['CANCELLATION', 'ORDER', 'RISK', 'SIGNAL']::TEXT[]
       AND durable_effect_types IS DISTINCT FROM ARRAY['ORDER', 'REJECTION', 'RISK', 'SIGNAL']::TEXT[]
    THEN
        RETURN FALSE;
    END IF;

    IF receipt_payload -> 'effects' IS DISTINCT FROM durable_effects
       OR jsonb_array_length(receipt_payload -> 'effects') <> durable_effect_count
    THEN
        RETURN FALSE;
    END IF;

    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(receipt_payload -> 'effects') item(value)
         WHERE jsonb_typeof(item.value) IS DISTINCT FROM 'object'
            OR NOT EXISTS (
                SELECT 1 FROM paper_effects effect
                 WHERE effect.job_run_id = receipt_entity_id::uuid
                   AND item.value = jsonb_build_object(
                       'effect_id', effect.effect_id::text,
                       'effect_type', effect.effect_type,
                       'entity_id', effect.entity_id::text,
                       'payload_hash', effect.payload_hash
                   )
            )
    ) THEN
        RETURN FALSE;
    END IF;

    IF EXISTS (
        SELECT 1 FROM paper_effects effect
         WHERE effect.job_run_id = receipt_entity_id::uuid
           AND NOT EXISTS (
               SELECT 1 FROM jsonb_array_elements(receipt_payload -> 'effects') item(value)
                WHERE item.value = jsonb_build_object(
                    'effect_id', effect.effect_id::text,
                    'effect_type', effect.effect_type,
                    'entity_id', effect.entity_id::text,
                    'payload_hash', effect.payload_hash
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
        SELECT 1 FROM audit_events
         WHERE event_type = 'PAPER_RUN_RECONCILED'
           AND NOT paper_reconciliation_audit_payload_matches_job(entity_id, payload)
    ) THEN
        RAISE EXCEPTION 'PAPER_RECONCILIATION_AUDIT_NO_TRADE_SHAPE_INVALID'
            USING ERRCODE = '23514';
    END IF;
END;
$$;
