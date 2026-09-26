-- PAPER halt/resume history is an operational audit trail.
-- Sequence order and transition timestamps must describe the same chronology.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM (
            SELECT
                created_at,
                lag(created_at) OVER (ORDER BY control_sequence) AS prior_created_at
            FROM paper_environment_control_events
        ) AS ordered_events
        WHERE prior_created_at IS NOT NULL
          AND created_at < prior_created_at
    ) THEN
        RAISE EXCEPTION 'PAPER_ENVIRONMENT_CONTROL_CHRONOLOGY_INVALID'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

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

    IF prior_created_at IS NOT NULL AND NEW.created_at < prior_created_at THEN
        RAISE EXCEPTION 'PAPER_ENVIRONMENT_CONTROL_CHRONOLOGY_INVALID'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;
