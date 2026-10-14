-- A FAILED PAPER job must not own committed durable effects.  Such a shape
-- contradicts the lifecycle claim that the execution failed and cannot be
-- safely replayed or skipped.  Recovery detects legacy corruption; prevent
-- new corruption at the terminal transition itself.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM job_runs run
          JOIN paper_effects effect
            ON effect.job_run_id = run.job_run_id
         WHERE run.status = 'FAILED'
    ) THEN
        RAISE EXCEPTION 'PAPER_JOB_FAILURE_WITH_DURABLE_EFFECTS'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_reject_failed_paper_job_with_effects()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'CLAIMED'
       AND NEW.status = 'FAILED'
       AND EXISTS (
           SELECT 1
             FROM paper_effects effect
            WHERE effect.job_run_id = OLD.job_run_id
       ) THEN
        RAISE EXCEPTION 'PAPER_JOB_FAILURE_WITH_DURABLE_EFFECTS'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_paper_failed_job_effect_guard ON job_runs;
CREATE TRIGGER trg_paper_failed_job_effect_guard
BEFORE UPDATE ON job_runs
FOR EACH ROW
EXECUTE FUNCTION hope_reject_failed_paper_job_with_effects();
