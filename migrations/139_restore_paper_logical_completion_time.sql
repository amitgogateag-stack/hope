-- PAPER completed_at is the logical execution time, while created_at records
-- when the durable claim row was persisted.  Replays and backfills may
-- legitimately persist after their logical completion, so only future-dated
-- completion timestamps are invalid.
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
