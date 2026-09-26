-- Preserve truthful autonomous PAPER claim chronology at insertion time.
-- Claim timestamps are already immutable; they must also be neither future
-- dated nor earlier than the scheduled invocation they claim.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM job_runs
         WHERE left(job_key, 6) = 'paper:'
           AND created_at > clock_timestamp()
    ) THEN
        RAISE EXCEPTION 'PAPER_JOB_CLAIM_TIMESTAMP_IN_FUTURE'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM job_runs
         WHERE left(job_key, 6) = 'paper:'
           AND created_at < scheduled_for
    ) THEN
        RAISE EXCEPTION 'PAPER_JOB_CLAIM_PRECEDES_SCHEDULE'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION guard_paper_job_claim_environment()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    current_state TEXT;
BEGIN
    IF left(NEW.job_key, 6) <> 'paper:' THEN
        RETURN NEW;
    END IF;

    IF NEW.scheduled_for > clock_timestamp() THEN
        RAISE EXCEPTION 'PAPER_JOB_CLAIM_SCHEDULED_IN_FUTURE'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.created_at > clock_timestamp() THEN
        RAISE EXCEPTION 'PAPER_JOB_CLAIM_TIMESTAMP_IN_FUTURE'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.created_at < NEW.scheduled_for THEN
        RAISE EXCEPTION 'PAPER_JOB_CLAIM_PRECEDES_SCHEDULE'
            USING ERRCODE = '23514';
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtext('hope:paper:environment-control')::bigint
    );

    SELECT state
      INTO current_state
      FROM paper_environment_control_events
     ORDER BY control_sequence DESC
     LIMIT 1;

    IF current_state IS DISTINCT FROM 'RUNNING' THEN
        RAISE EXCEPTION 'PAPER_JOB_CLAIM_REQUIRES_RUNNING_ENVIRONMENT'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;
