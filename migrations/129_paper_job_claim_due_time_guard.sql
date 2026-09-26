-- Autonomous PAPER job rows represent claims, not future schedule declarations.
-- Reject legacy or new claims whose scheduled invocation has not become due.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM job_runs
         WHERE status = 'CLAIMED'
           AND left(job_key, 6) = 'paper:'
           AND scheduled_for > clock_timestamp()
    ) THEN
        RAISE EXCEPTION 'PAPER_JOB_CLAIM_SCHEDULED_IN_FUTURE'
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
