-- A canonical PAPER risk effect owned by a different job can only reuse
-- its source SIGNAL effect after that source job has succeeded. This keeps
-- durable risk truth aligned with the repository's recovery contract.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM paper_risk_assessments assessment
          JOIN paper_effects risk_effect
            ON risk_effect.effect_type = 'RISK'
           AND risk_effect.entity_id = assessment.signal_id
          LEFT JOIN paper_effects signal_effect
            ON signal_effect.effect_type = 'SIGNAL'
           AND signal_effect.entity_id = assessment.signal_id
          LEFT JOIN job_runs signal_owner
            ON signal_owner.job_run_id = signal_effect.job_run_id
         WHERE signal_effect.effect_id IS NULL
            OR (
                signal_effect.job_run_id <> risk_effect.job_run_id
                AND signal_owner.status IS DISTINCT FROM 'SUCCEEDED'
            )
    ) THEN
        RAISE EXCEPTION 'PAPER_RISK_SOURCE_SIGNAL_LINEAGE_CONFLICT'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_require_paper_risk_signal_effect_lineage()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    risk_effect_owner UUID;
    signal_effect_owner UUID;
    signal_owner_status TEXT;
BEGIN
    SELECT job_run_id
      INTO risk_effect_owner
      FROM paper_effects
     WHERE effect_type = 'RISK'
       AND entity_id = NEW.signal_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'PAPER_RISK_SOURCE_SIGNAL_LINEAGE_CONFLICT'
            USING ERRCODE = '23514';
    END IF;

    SELECT job_run_id
      INTO signal_effect_owner
      FROM paper_effects
     WHERE effect_type = 'SIGNAL'
       AND entity_id = NEW.signal_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'PAPER_RISK_SOURCE_SIGNAL_LINEAGE_CONFLICT'
            USING ERRCODE = '23514';
    END IF;

    IF signal_effect_owner <> risk_effect_owner THEN
        SELECT status
          INTO signal_owner_status
          FROM job_runs
         WHERE job_run_id = signal_effect_owner
         FOR UPDATE;
        IF NOT FOUND OR signal_owner_status <> 'SUCCEEDED' THEN
            RAISE EXCEPTION 'PAPER_RISK_SOURCE_SIGNAL_LINEAGE_CONFLICT'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_zzzz_paper_risk_signal_effect_lineage
AFTER INSERT ON paper_risk_assessments
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION hope_require_paper_risk_signal_effect_lineage();
