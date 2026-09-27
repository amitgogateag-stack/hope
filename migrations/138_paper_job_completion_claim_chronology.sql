-- A terminal PAPER result cannot exist before the durable claim that
-- authorized its execution.  Reject existing impossible history before
-- strengthening the update guard for every future completion.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM job_runs
         WHERE left(job_key, 6) = 'paper:'
           AND status IN ('SUCCEEDED', 'FAILED')
           AND completed_at < created_at
    ) THEN
        RAISE EXCEPTION 'PAPER_JOB_COMPLETION_PRECEDES_CLAIM'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION guard_paper_job_completion_timestamp()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF left(NEW.job_key, 6) = 'paper:'
       AND NEW.status IN ('SUCCEEDED', 'FAILED')
    THEN
        IF NEW.completed_at > clock_timestamp() THEN
            RAISE EXCEPTION 'PAPER_JOB_COMPLETION_TIMESTAMP_IN_FUTURE'
                USING ERRCODE = '23514';
        END IF;

        IF NEW.completed_at < NEW.created_at THEN
            RAISE EXCEPTION 'PAPER_JOB_COMPLETION_PRECEDES_CLAIM'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;
