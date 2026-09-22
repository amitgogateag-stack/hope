-- Preserve the original durable claim timestamp throughout the job lifecycle.
CREATE OR REPLACE FUNCTION hope_guard_job_run_identity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.job_run_id IS DISTINCT FROM OLD.job_run_id
       OR NEW.job_key IS DISTINCT FROM OLD.job_key
       OR NEW.scheduled_for IS DISTINCT FROM OLD.scheduled_for
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'JOB_RUN_IDENTITY_IMMUTABLE: scheduled job identity and claim time cannot be modified'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
