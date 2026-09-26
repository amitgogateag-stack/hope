-- Serialize autonomous PAPER claims with global halt/resume transitions.
-- A direct SQL claim must not start while the environment is HALTED, and the
-- shared advisory lock closes the race between a new claim and a resume check.
CREATE OR REPLACE FUNCTION guard_paper_job_claim_environment()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    current_state TEXT;
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

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_job_claim_environment_guard
BEFORE INSERT ON job_runs
FOR EACH ROW
EXECUTE FUNCTION guard_paper_job_claim_environment();
