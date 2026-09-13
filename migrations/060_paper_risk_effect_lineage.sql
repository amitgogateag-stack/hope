-- Persisted PAPER risk decisions must be backed by durable SIGNAL and RISK effects.
CREATE OR REPLACE FUNCTION hope_require_paper_risk_effect_lineage()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM paper_effects
        WHERE effect_type = 'SIGNAL' AND entity_id = NEW.signal_id
    ) THEN
        RAISE EXCEPTION 'PAPER_RISK_SOURCE_SIGNAL_UNTRACKED: signal % has no durable SIGNAL effect', NEW.signal_id
            USING ERRCODE = '23514';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM paper_effects
        WHERE effect_type = 'RISK' AND entity_id = NEW.signal_id
    ) THEN
        RAISE EXCEPTION 'PAPER_RISK_EFFECT_UNTRACKED: signal % has no durable RISK effect', NEW.signal_id
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_paper_risk_assessment_requires_effect_lineage ON paper_risk_assessments;
CREATE TRIGGER trg_paper_risk_assessment_requires_effect_lineage
BEFORE INSERT ON paper_risk_assessments
FOR EACH ROW EXECUTE FUNCTION hope_require_paper_risk_effect_lineage();
