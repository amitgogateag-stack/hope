-- Enforce PAPER environment transition semantics at the storage boundary.
-- Direct SQL must obey the same serialized no-op rejection as the repository API.
CREATE OR REPLACE FUNCTION guard_paper_environment_control_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    prior_state TEXT;
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtext('hope:paper:environment-control')::bigint
    );

    SELECT state
      INTO prior_state
      FROM paper_environment_control_events
     ORDER BY control_sequence DESC
     LIMIT 1;

    IF prior_state IS NOT NULL AND prior_state = NEW.state THEN
        RAISE EXCEPTION 'PAPER_ENVIRONMENT_CONTROL_NOOP_TRANSITION'
            USING ERRCODE='23514';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_paper_environment_control_insert_guard
    ON paper_environment_control_events;

CREATE TRIGGER trg_paper_environment_control_insert_guard
BEFORE INSERT ON paper_environment_control_events
FOR EACH ROW
EXECUTE FUNCTION guard_paper_environment_control_insert();
