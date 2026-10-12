-- Bind durable PAPER risk assessments to their exact RISK effects.
-- Application recovery validates this pair, but PostgreSQL must also reject
-- conflicting or one-sided authorization evidence at the transaction boundary.
CREATE OR REPLACE FUNCTION hope_paper_risk_payload_hash(
    signal_id UUID,
    decision TEXT,
    reason_code TEXT,
    approved_quantity NUMERIC
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
                decision || '|' ||
                reason_code || '|' ||
                trim_scale(approved_quantity)::TEXT,
                'UTF8'
            ),
            'sha256'
        ),
        'hex'
    );
$$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM paper_risk_assessments assessment
          LEFT JOIN paper_effects effect
            ON effect.effect_type = 'RISK'
           AND effect.entity_id = assessment.signal_id
         WHERE effect.effect_id IS NULL
    ) THEN
        RAISE EXCEPTION 'PAPER_RISK_ASSESSMENT_WITHOUT_EFFECT'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM paper_effects effect
          LEFT JOIN paper_risk_assessments assessment
            ON assessment.signal_id = effect.entity_id
         WHERE effect.effect_type = 'RISK'
           AND assessment.signal_id IS NULL
    ) THEN
        RAISE EXCEPTION 'PAPER_RISK_EFFECT_WITHOUT_ASSESSMENT'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM paper_risk_assessments assessment
          JOIN paper_effects effect
            ON effect.effect_type = 'RISK'
           AND effect.entity_id = assessment.signal_id
         WHERE effect.payload_hash <> hope_paper_risk_payload_hash(
             assessment.signal_id,
             assessment.decision,
             assessment.reason_code,
             assessment.approved_quantity
         )
    ) THEN
        RAISE EXCEPTION 'PAPER_RISK_EFFECT_PAYLOAD_MISMATCH'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_lock_paper_risk_payload_identity(
    target_signal_id UUID
)
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended('hope:paper:risk-payload:' || target_signal_id, 0)
    );
END;
$$;

CREATE OR REPLACE FUNCTION hope_guard_paper_risk_assessment_payload()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    durable_payload_hash TEXT;
BEGIN
    PERFORM hope_lock_paper_risk_payload_identity(NEW.signal_id);

    SELECT payload_hash
      INTO durable_payload_hash
      FROM paper_effects
     WHERE effect_type = 'RISK'
       AND entity_id = NEW.signal_id;

    IF FOUND AND durable_payload_hash <> hope_paper_risk_payload_hash(
        NEW.signal_id,
        NEW.decision,
        NEW.reason_code,
        NEW.approved_quantity
    ) THEN
        RAISE EXCEPTION 'PAPER_RISK_EFFECT_PAYLOAD_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_zzzz_paper_risk_assessment_payload
BEFORE INSERT ON paper_risk_assessments
FOR EACH ROW
EXECUTE FUNCTION hope_guard_paper_risk_assessment_payload();

CREATE OR REPLACE FUNCTION hope_guard_paper_risk_effect_payload()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    durable_assessment RECORD;
BEGIN
    IF NEW.effect_type <> 'RISK' THEN
        RETURN NEW;
    END IF;

    PERFORM hope_lock_paper_risk_payload_identity(NEW.entity_id);

    SELECT signal_id, decision, reason_code, approved_quantity
      INTO durable_assessment
      FROM paper_risk_assessments
     WHERE signal_id = NEW.entity_id;

    IF FOUND AND NEW.payload_hash <> hope_paper_risk_payload_hash(
        durable_assessment.signal_id,
        durable_assessment.decision,
        durable_assessment.reason_code,
        durable_assessment.approved_quantity
    ) THEN
        RAISE EXCEPTION 'PAPER_RISK_EFFECT_PAYLOAD_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_zzzzzzz_paper_risk_effect_payload
BEFORE INSERT ON paper_effects
FOR EACH ROW
EXECUTE FUNCTION hope_guard_paper_risk_effect_payload();

CREATE OR REPLACE FUNCTION hope_require_paper_risk_effect()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM hope_lock_paper_risk_payload_identity(NEW.signal_id);

    IF NOT EXISTS (
        SELECT 1
          FROM paper_effects
         WHERE effect_type = 'RISK'
           AND entity_id = NEW.signal_id
    ) THEN
        RAISE EXCEPTION 'PAPER_RISK_ASSESSMENT_WITHOUT_EFFECT'
            USING ERRCODE = '23514';
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_paper_risk_assessment_effect_complete
AFTER INSERT ON paper_risk_assessments
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION hope_require_paper_risk_effect();

CREATE OR REPLACE FUNCTION hope_require_paper_risk_assessment()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.effect_type <> 'RISK' THEN
        RETURN NULL;
    END IF;

    PERFORM hope_lock_paper_risk_payload_identity(NEW.entity_id);

    IF NOT EXISTS (
        SELECT 1
          FROM paper_risk_assessments
         WHERE signal_id = NEW.entity_id
    ) THEN
        RAISE EXCEPTION 'PAPER_RISK_EFFECT_WITHOUT_ASSESSMENT'
            USING ERRCODE = '23514';
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_paper_risk_effect_assessment_complete
AFTER INSERT ON paper_effects
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION hope_require_paper_risk_assessment();
