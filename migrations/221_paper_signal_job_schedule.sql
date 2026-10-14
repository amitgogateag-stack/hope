-- A durable PAPER SIGNAL must never claim information from after the
-- schedule of the job that owns its immutable effect. Application jobs enforce
-- this at normal runtime; PostgreSQL must also reject direct repository/SQL
-- writes and must do so regardless of signal/effect insertion order.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM signals signal
          JOIN paper_effects effect
            ON effect.effect_type = 'SIGNAL'
           AND effect.entity_id = signal.signal_id
          JOIN job_runs owner
            ON owner.job_run_id = effect.job_run_id
         WHERE signal.decision_time > owner.scheduled_for
    ) THEN
        RAISE EXCEPTION 'PAPER_SIGNAL_JOB_SCHEDULE_CONFLICT'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_require_paper_signal_job_schedule_from_signal()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    owner_schedule TIMESTAMPTZ;
BEGIN
    SELECT owner.scheduled_for
      INTO owner_schedule
      FROM paper_effects effect
      JOIN job_runs owner
        ON owner.job_run_id = effect.job_run_id
     WHERE effect.effect_type = 'SIGNAL'
       AND effect.entity_id = NEW.signal_id;

    -- Existing reciprocal completeness guards retain the specific missing
    -- effect diagnostic when the paired effect is absent at commit.
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    IF NEW.decision_time > owner_schedule THEN
        RAISE EXCEPTION 'PAPER_SIGNAL_AFTER_JOB_SCHEDULE'
            USING ERRCODE = '23514';
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_zzzzzzz_paper_signal_job_schedule
AFTER INSERT ON signals
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION hope_require_paper_signal_job_schedule_from_signal();

CREATE OR REPLACE FUNCTION hope_require_paper_signal_job_schedule_from_effect()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    durable_decision_time TIMESTAMPTZ;
    owner_schedule TIMESTAMPTZ;
BEGIN
    IF NEW.effect_type <> 'SIGNAL' THEN
        RETURN NULL;
    END IF;

    SELECT signal.decision_time, owner.scheduled_for
      INTO durable_decision_time, owner_schedule
      FROM signals signal
      JOIN job_runs owner
        ON owner.job_run_id = NEW.job_run_id
     WHERE signal.signal_id = NEW.entity_id;

    -- Existing reciprocal completeness guards retain the specific missing
    -- signal diagnostic when the paired row is absent at commit.
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    IF durable_decision_time > owner_schedule THEN
        RAISE EXCEPTION 'PAPER_SIGNAL_AFTER_JOB_SCHEDULE'
            USING ERRCODE = '23514';
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_zzzzzzz_paper_effect_signal_job_schedule
AFTER INSERT ON paper_effects
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION hope_require_paper_signal_job_schedule_from_effect();
