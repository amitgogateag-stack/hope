-- Durable PAPER terminal truth must not occur after the schedule of the
-- job that owns its immutable CANCELLATION or REJECTION effect. Runtime
-- callbacks enforce this on normal paths; PostgreSQL must protect direct
-- repository/SQL writes regardless of terminal/effect insertion order.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM paper_order_terminal_events terminal
          JOIN paper_effects effect
            ON effect.entity_id = terminal.order_id
           AND effect.effect_type = CASE terminal.outcome
               WHEN 'CANCELLED' THEN 'CANCELLATION'
               WHEN 'REJECTED' THEN 'REJECTION'
           END
          JOIN job_runs owner
            ON owner.job_run_id = effect.job_run_id
         WHERE terminal.event_time > owner.scheduled_for
    ) THEN
        RAISE EXCEPTION 'PAPER_TERMINAL_JOB_SCHEDULE_CONFLICT'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_require_paper_terminal_job_schedule_from_event()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    owner_schedule TIMESTAMPTZ;
    required_effect_type TEXT;
BEGIN
    required_effect_type := CASE NEW.outcome
        WHEN 'CANCELLED' THEN 'CANCELLATION'
        WHEN 'REJECTED' THEN 'REJECTION'
    END;

    SELECT owner.scheduled_for
      INTO owner_schedule
      FROM paper_effects effect
      JOIN job_runs owner
        ON owner.job_run_id = effect.job_run_id
     WHERE effect.effect_type = required_effect_type
       AND effect.entity_id = NEW.order_id;

    -- Existing reciprocal completeness guards retain the specific missing
    -- effect diagnostic when the paired effect is absent at commit.
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    IF NEW.event_time > owner_schedule THEN
        RAISE EXCEPTION 'PAPER_TERMINAL_AFTER_JOB_SCHEDULE'
            USING ERRCODE = '23514';
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_zzzzzzzz_paper_terminal_job_schedule
AFTER INSERT ON paper_order_terminal_events
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION hope_require_paper_terminal_job_schedule_from_event();

CREATE OR REPLACE FUNCTION hope_require_paper_terminal_job_schedule_from_effect()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    durable_event_time TIMESTAMPTZ;
    owner_schedule TIMESTAMPTZ;
BEGIN
    IF NEW.effect_type NOT IN ('CANCELLATION', 'REJECTION') THEN
        RETURN NULL;
    END IF;

    SELECT terminal.event_time, owner.scheduled_for
      INTO durable_event_time, owner_schedule
      FROM paper_order_terminal_events terminal
      JOIN job_runs owner
        ON owner.job_run_id = NEW.job_run_id
     WHERE terminal.order_id = NEW.entity_id
       AND NEW.effect_type = CASE terminal.outcome
           WHEN 'CANCELLED' THEN 'CANCELLATION'
           WHEN 'REJECTED' THEN 'REJECTION'
       END;

    -- Existing reciprocal completeness guards retain the specific missing
    -- event diagnostic when the paired row is absent at commit.
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    IF durable_event_time > owner_schedule THEN
        RAISE EXCEPTION 'PAPER_TERMINAL_AFTER_JOB_SCHEDULE'
            USING ERRCODE = '23514';
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_zzzzzzzz_paper_effect_terminal_job_schedule
AFTER INSERT ON paper_effects
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION hope_require_paper_terminal_job_schedule_from_effect();
