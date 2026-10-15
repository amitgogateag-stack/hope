-- NO_TRADE is a complete zero-signal decision, so it cannot coexist with any
-- other effect owned by the same PAPER job.  Serialize all effect inserts for
-- one job before checking, closing both insertion orders and concurrency races.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM paper_effects no_trade
         WHERE no_trade.effect_type = 'NO_TRADE'
           AND EXISTS (
               SELECT 1
                 FROM paper_effects other
                WHERE other.job_run_id = no_trade.job_run_id
                  AND other.effect_id <> no_trade.effect_id
           )
    ) THEN
        RAISE EXCEPTION 'PAPER_NO_TRADE_EFFECT_EXCLUSIVITY'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_guard_paper_no_trade_effect_exclusivity()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM 1
      FROM job_runs
     WHERE job_run_id = NEW.job_run_id
     FOR UPDATE;

    IF NEW.effect_type = 'NO_TRADE' THEN
        IF EXISTS (
            SELECT 1
              FROM paper_effects effect
             WHERE effect.job_run_id = NEW.job_run_id
               AND effect.effect_id <> NEW.effect_id
        ) THEN
            RAISE EXCEPTION 'PAPER_NO_TRADE_EFFECT_EXCLUSIVITY'
                USING ERRCODE = '23514';
        END IF;
    ELSIF EXISTS (
        SELECT 1
          FROM paper_effects effect
         WHERE effect.job_run_id = NEW.job_run_id
           AND effect.effect_type = 'NO_TRADE'
    ) THEN
        RAISE EXCEPTION 'PAPER_NO_TRADE_EFFECT_EXCLUSIVITY'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_no_trade_effect_exclusivity
BEFORE INSERT ON paper_effects
FOR EACH ROW EXECUTE FUNCTION hope_guard_paper_no_trade_effect_exclusivity();
