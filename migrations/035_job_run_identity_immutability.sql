-- Preserve durable scheduled-run identity from claim through terminal completion.
CREATE OR REPLACE FUNCTION hope_guard_job_run_identity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.job_run_id IS DISTINCT FROM OLD.job_run_id
       OR NEW.job_key IS DISTINCT FROM OLD.job_key
       OR NEW.scheduled_for IS DISTINCT FROM OLD.scheduled_for THEN
        RAISE EXCEPTION 'JOB_RUN_IDENTITY_IMMUTABLE: scheduled job identity cannot be modified'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_job_run_identity_immutability
BEFORE UPDATE ON job_runs
FOR EACH ROW
EXECUTE FUNCTION hope_guard_job_run_identity();
