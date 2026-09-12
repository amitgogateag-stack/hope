-- Preserve durable terminal job lineage once a scheduled run completes.
CREATE OR REPLACE FUNCTION hope_reject_terminal_job_run_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.status IN ('SUCCEEDED', 'FAILED') THEN
        RAISE EXCEPTION 'JOB_RUN_TERMINAL_IMMUTABLE: terminal job history cannot be modified or deleted'
            USING ERRCODE = '23514';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;

CREATE TRIGGER trg_job_run_terminal_immutability
BEFORE UPDATE OR DELETE ON job_runs
FOR EACH ROW
EXECUTE FUNCTION hope_reject_terminal_job_run_mutation();
