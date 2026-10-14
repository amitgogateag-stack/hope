-- A durable PAPER fill must not occur after the schedule of the job
-- that owns its immutable FILL effect. Runtime jobs enforce this on normal
-- paths; PostgreSQL must also reject direct repository/SQL writes regardless
-- of fill/effect insertion order.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM fills fill
          JOIN paper_effects effect
            ON effect.effect_type = 'FILL'
           AND effect.entity_id = fill.fill_id
          JOIN job_runs owner
            ON owner.job_run_id = effect.job_run_id
         WHERE fill.filled_at > owner.scheduled_for
    ) THEN
        RAISE EXCEPTION 'PAPER_FILL_JOB_SCHEDULE_CONFLICT'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_require_paper_fill_job_schedule_from_fill()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    owner_schedule TIMESTAMPTZ;
BEGIN
    SELECT owner.scheduled_for
      INTO owner_schedule
      FROM paper_effects effect
      JOIN job_runs owner
        ON owner.job_run_id = effect.job_run_id
     WHERE effect.effect_type = 'FILL'
       AND effect.entity_id = NEW.fill_id;

    -- Existing reciprocal completeness guards retain the specific missing
    -- effect diagnostic when the paired effect is absent at commit.
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    IF NEW.filled_at > owner_schedule THEN
        RAISE EXCEPTION 'PAPER_FILL_AFTER_JOB_SCHEDULE'
            USING ERRCODE = '23514';
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_zzzzzzz_paper_fill_job_schedule
AFTER INSERT ON fills
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION hope_require_paper_fill_job_schedule_from_fill();

CREATE OR REPLACE FUNCTION hope_require_paper_fill_job_schedule_from_effect()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    durable_fill_time TIMESTAMPTZ;
    owner_schedule TIMESTAMPTZ;
BEGIN
    IF NEW.effect_type <> 'FILL' THEN
        RETURN NULL;
    END IF;

    SELECT fill.filled_at, owner.scheduled_for
      INTO durable_fill_time, owner_schedule
      FROM fills fill
      JOIN job_runs owner
        ON owner.job_run_id = NEW.job_run_id
     WHERE fill.fill_id = NEW.entity_id;

    -- Existing reciprocal completeness guards retain the specific missing
    -- fill diagnostic when the paired row is absent at commit.
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    IF durable_fill_time > owner_schedule THEN
        RAISE EXCEPTION 'PAPER_FILL_AFTER_JOB_SCHEDULE'
            USING ERRCODE = '23514';
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_zzzzzzz_paper_effect_fill_job_schedule
AFTER INSERT ON paper_effects
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION hope_require_paper_fill_job_schedule_from_effect();
