-- A PAPER control generator positioned at the current history maximum with
-- is_called=false will reuse that primary-key value on the next default insert.
-- Treat that state as corruption during migration/startup validation rather than
-- allowing the control plane to discover it only when a halt/resume is attempted.
DO $$
DECLARE
    history_max BIGINT;
    generator_last BIGINT;
    generator_is_called BOOLEAN;
BEGIN
    SELECT max(control_sequence)
      INTO history_max
      FROM paper_environment_control_events;

    SELECT last_value, is_called
      INTO generator_last, generator_is_called
      FROM paper_environment_control_events_control_sequence_seq;

    IF history_max IS NULL THEN
        RAISE EXCEPTION 'PAPER_ENVIRONMENT_CONTROL_STATE_MISSING'
            USING ERRCODE = '23514';
    END IF;

    IF generator_last < history_max
       OR (generator_last = history_max AND generator_is_called IS NOT TRUE)
    THEN
        RAISE EXCEPTION 'PAPER_ENVIRONMENT_CONTROL_SEQUENCE_GENERATOR_NOT_READY'
            USING ERRCODE = '23514';
    END IF;
END;
$$;
