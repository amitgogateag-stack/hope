-- PAPER control sequence values must remain backed by the database sequence.
-- An explicit jump above the generator can otherwise poison future legitimate
-- transitions: subsequent BIGSERIAL defaults would be <= the stored maximum and
-- fail the monotonic chronology guard until the sequence eventually catches up.

DO $$
DECLARE
    history_max BIGINT;
    generator_last BIGINT;
BEGIN
    SELECT max(control_sequence)
      INTO history_max
      FROM paper_environment_control_events;

    SELECT last_value
      INTO generator_last
      FROM paper_environment_control_events_control_sequence_seq;

    IF history_max IS NOT NULL AND history_max > generator_last THEN
        RAISE EXCEPTION 'PAPER_ENVIRONMENT_CONTROL_SEQUENCE_GENERATOR_BEHIND_HISTORY'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION guard_paper_environment_control_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    prior_sequence BIGINT;
    prior_state TEXT;
    prior_created_at TIMESTAMPTZ;
    generator_last BIGINT;
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtext('hope:paper:environment-control')::bigint
    );

    SELECT control_sequence, state, created_at
      INTO prior_sequence, prior_state, prior_created_at
      FROM paper_environment_control_events
     ORDER BY control_sequence DESC
     LIMIT 1;

    SELECT last_value
      INTO generator_last
      FROM paper_environment_control_events_control_sequence_seq;

    IF prior_state IS NOT NULL AND prior_state = NEW.state THEN
        RAISE EXCEPTION 'PAPER_ENVIRONMENT_CONTROL_NOOP_TRANSITION'
            USING ERRCODE = '23514';
    END IF;

    IF prior_sequence IS NOT NULL AND NEW.control_sequence <= prior_sequence THEN
        RAISE EXCEPTION 'PAPER_ENVIRONMENT_CONTROL_SEQUENCE_NOT_MONOTONIC'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.control_sequence > generator_last THEN
        RAISE EXCEPTION 'PAPER_ENVIRONMENT_CONTROL_SEQUENCE_NOT_GENERATED'
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
