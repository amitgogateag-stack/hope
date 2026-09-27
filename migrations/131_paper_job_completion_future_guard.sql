-- Terminal PAPER lifecycle timestamps must describe an event that has
-- already happened.  Prevent direct SQL or application defects from recording
-- a completion timestamp in the future.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM job_runs
         WHERE left(job_key, 6) = 'paper:'
           AND status IN ('SUCCEEDED', 'FAILED')
           AND completed_at > clock_timestamp()
    ) THEN
        RAISE EXCEPTION 'PAPER_JOB_COMPLETION_TIMESTAMP_IN_FUTURE'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION guard_paper_job_completion_timestamp()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF left(NEW.job_key, 6) = 'paper:'
       AND NEW.status IN ('SUCCEEDED', 'FAILED')
       AND NEW.completed_at > clock_timestamp()
    THEN
        RAISE EXCEPTION 'PAPER_JOB_COMPLETION_TIMESTAMP_IN_FUTURE'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_job_completion_timestamp_guard
BEFORE UPDATE ON job_runs
FOR EACH ROW
EXECUTE FUNCTION guard_paper_job_completion_timestamp();
