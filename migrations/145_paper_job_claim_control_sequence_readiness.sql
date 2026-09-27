-- A direct PAPER claim must not proceed when the global halt/resume
-- sequence generator cannot safely append the next control event.  Otherwise a
-- job could begin while the kill switch is operationally unable to record a
-- subsequent HALTED transition.
CREATE OR REPLACE FUNCTION guard_paper_job_claim_environment()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    current_state TEXT;
    history_max BIGINT;
    generator_last BIGINT;
    generator_is_called BOOLEAN;
BEGIN
    IF left(NEW.job_key, 6) <> 'paper:' THEN
        RETURN NEW;
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

    SELECT max(control_sequence)
      INTO history_max
      FROM paper_environment_control_events;

    SELECT last_value, is_called
      INTO generator_last, generator_is_called
      FROM paper_environment_control_events_control_sequence_seq;

    IF history_max IS NULL
       OR generator_last < history_max
       OR (generator_last = history_max AND generator_is_called IS NOT TRUE)
    THEN
        RAISE EXCEPTION 'PAPER_JOB_CLAIM_REQUIRES_CONTROL_SEQUENCE_READY'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;
