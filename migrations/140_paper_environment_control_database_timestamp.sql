-- PAPER halt/resume events are an authoritative operational audit trail.
-- New transitions must use PostgreSQL's authenticated transaction timestamp;
-- callers cannot supply an alternate timestamp that merely fits between the
-- chronology and wall-clock guards.
CREATE OR REPLACE FUNCTION guard_paper_environment_control_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    prior_state TEXT;
    prior_created_at TIMESTAMPTZ;
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtext('hope:paper:environment-control')::bigint
    );

    SELECT state, created_at
      INTO prior_state, prior_created_at
      FROM paper_environment_control_events
     ORDER BY control_sequence DESC
     LIMIT 1;

    IF prior_state IS NOT NULL AND prior_state = NEW.state THEN
        RAISE EXCEPTION 'PAPER_ENVIRONMENT_CONTROL_NOOP_TRANSITION'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.created_at > clock_timestamp() THEN
        RAISE EXCEPTION 'PAPER_ENVIRONMENT_CONTROL_TIMESTAMP_IN_FUTURE'
            USING ERRCODE = '23514';
    END IF;

    IF prior_created_at IS NOT NULL AND NEW.created_at < prior_created_at THEN
        RAISE EXCEPTION 'PAPER_ENVIRONMENT_CONTROL_CHRONOLOGY_INVALID'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.created_at IS DISTINCT FROM transaction_timestamp() THEN
        RAISE EXCEPTION 'PAPER_ENVIRONMENT_CONTROL_TIMESTAMP_NOT_DATABASE_AUTHENTICATED'
            USING ERRCODE = '23514';
    END IF;

    IF prior_state = 'HALTED' AND NEW.state = 'RUNNING'
       AND EXISTS (
           SELECT 1
             FROM job_runs
            WHERE status = 'CLAIMED'
              AND left(job_key, 6) = 'paper:'
       )
    THEN
        RAISE EXCEPTION 'PAPER_ENVIRONMENT_RESUME_BLOCKED_BY_INCOMPLETE_CLAIM'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;
