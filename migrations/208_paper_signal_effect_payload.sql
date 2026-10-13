-- Authenticate canonical PAPER signals against their durable SIGNAL effects.
-- Historical signals created before migration 168 may have all recovery
-- columns NULL; recovery already fails closed for those rows.  New/partially
-- materialized rows must never be allowed to look recoverable when they are not.
CREATE OR REPLACE FUNCTION hope_paper_signal_payload_hash(
    signal_id UUID,
    instrument_id UUID,
    strategy_version TEXT,
    decision_time TIMESTAMPTZ,
    signal_type TEXT,
    conviction NUMERIC,
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
                signal_id::TEXT || '|' ||
                instrument_id::TEXT || '|' ||
                btrim(strategy_version) || '|' ||
                to_char(
                    decision_time AT TIME ZONE 'UTC',
                    'YYYY-MM-DD"T"HH24:MI:SS'
                ) ||
                CASE
                    WHEN (extract(microseconds FROM decision_time)::INTEGER %% 1000000) = 0
                    THEN ''
                    ELSE '.' || to_char(decision_time AT TIME ZONE 'UTC', 'US')
                END ||
                '+00:00|' ||
                signal_type || '|' ||
                trim_scale(conviction)::TEXT || '|' ||
                inputs_hash,
                'UTF8'
            ),
            'sha256'
        ),
        'hex'
    );
$$;

CREATE OR REPLACE FUNCTION hope_paper_signal_recovery_material_state(
    strategy_version TEXT,
    signal_type TEXT,
    conviction NUMERIC,
    inputs_hash TEXT
)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT CASE
        WHEN strategy_version IS NULL
         AND signal_type IS NULL
         AND conviction IS NULL
         AND inputs_hash IS NULL
        THEN 'LEGACY'
        WHEN strategy_version IS NOT NULL
         AND signal_type IS NOT NULL
         AND conviction IS NOT NULL
         AND inputs_hash IS NOT NULL
        THEN 'COMPLETE'
        ELSE 'PARTIAL'
    END;
$$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM signals source_signal
          JOIN paper_effects effect
            ON effect.effect_type = 'SIGNAL'
           AND effect.entity_id = source_signal.signal_id
         WHERE source_signal.state = 'SIGNAL'
           AND hope_paper_signal_recovery_material_state(
             source_signal.strategy_version,
             source_signal.signal_type,
             source_signal.conviction,
             source_signal.inputs_hash
         ) = 'PARTIAL'
    ) THEN
        RAISE EXCEPTION 'PAPER_SIGNAL_RECOVERY_MATERIAL_PARTIAL'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM signals source_signal
          JOIN paper_effects effect
            ON effect.effect_type = 'SIGNAL'
           AND effect.entity_id = source_signal.signal_id
         WHERE source_signal.state = 'SIGNAL'
           AND hope_paper_signal_recovery_material_state(
             source_signal.strategy_version,
             source_signal.signal_type,
             source_signal.conviction,
             source_signal.inputs_hash
         ) = 'COMPLETE'
           AND effect.payload_hash <> hope_paper_signal_payload_hash(
               source_signal.signal_id,
               source_signal.instrument_id,
               source_signal.strategy_version,
               source_signal.decision_time,
               source_signal.signal_type,
               source_signal.conviction,
               source_signal.inputs_hash
           )
    ) THEN
        RAISE EXCEPTION 'PAPER_SIGNAL_EFFECT_PAYLOAD_MISMATCH'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_lock_paper_signal_payload_identity(
    target_signal_id UUID
)
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended('hope:paper:signal-payload:' || target_signal_id, 0)
    );
END;
$$;

CREATE OR REPLACE FUNCTION hope_assert_paper_signal_payload(
    target_signal_id UUID,
    target_instrument_id UUID,
    target_state TEXT,
    target_strategy_version TEXT,
    target_decision_time TIMESTAMPTZ,
    target_signal_type TEXT,
    target_conviction NUMERIC,
    target_inputs_hash TEXT,
    target_payload_hash TEXT
)
RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE
    material_state TEXT;
BEGIN
    -- NO_SIGNAL and STALE_SIGNAL rows are research/audit decisions. Existing
    -- PNL lineage constraints decide whether they may authorize accounting;
    -- this function authenticates only canonical actionable PAPER signals.
    IF target_state <> 'SIGNAL' THEN
        RETURN;
    END IF;

    material_state := hope_paper_signal_recovery_material_state(
        target_strategy_version,
        target_signal_type,
        target_conviction,
        target_inputs_hash
    );

    IF material_state = 'LEGACY' THEN
        RETURN;
    END IF;
    IF material_state = 'PARTIAL' THEN
        RAISE EXCEPTION 'PAPER_SIGNAL_RECOVERY_MATERIAL_PARTIAL'
            USING ERRCODE = '23514';
    END IF;

    IF target_payload_hash <> hope_paper_signal_payload_hash(
        target_signal_id,
        target_instrument_id,
        target_strategy_version,
        target_decision_time,
        target_signal_type,
        target_conviction,
        target_inputs_hash
    ) THEN
        RAISE EXCEPTION 'PAPER_SIGNAL_EFFECT_PAYLOAD_MISMATCH'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_guard_paper_signal_payload()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    durable_payload_hash TEXT;
BEGIN
    PERFORM hope_lock_paper_signal_payload_identity(NEW.signal_id);

    SELECT payload_hash
      INTO durable_payload_hash
      FROM paper_effects
     WHERE effect_type = 'SIGNAL'
       AND entity_id = NEW.signal_id;

    IF FOUND THEN
        PERFORM hope_assert_paper_signal_payload(
            NEW.signal_id,
            NEW.instrument_id,
            NEW.state,
            NEW.strategy_version,
            NEW.decision_time,
            NEW.signal_type,
            NEW.conviction,
            NEW.inputs_hash,
            durable_payload_hash
        );
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_zzzz_paper_signal_payload
BEFORE INSERT ON signals
FOR EACH ROW
EXECUTE FUNCTION hope_guard_paper_signal_payload();

CREATE OR REPLACE FUNCTION hope_guard_paper_signal_effect_payload()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    durable_signal RECORD;
BEGIN
    IF NEW.effect_type <> 'SIGNAL' THEN
        RETURN NEW;
    END IF;

    PERFORM hope_lock_paper_signal_payload_identity(NEW.entity_id);

    SELECT signal_id, instrument_id, state, strategy_version, decision_time,
           signal_type, conviction, inputs_hash
      INTO durable_signal
      FROM signals
     WHERE signal_id = NEW.entity_id;

    IF FOUND THEN
        PERFORM hope_assert_paper_signal_payload(
            durable_signal.signal_id,
            durable_signal.instrument_id,
            durable_signal.state,
            durable_signal.strategy_version,
            durable_signal.decision_time,
            durable_signal.signal_type,
            durable_signal.conviction,
            durable_signal.inputs_hash,
            NEW.payload_hash
        );
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_zzzzzzzzz_paper_signal_effect_payload
BEFORE INSERT ON paper_effects
FOR EACH ROW
EXECUTE FUNCTION hope_guard_paper_signal_effect_payload();
