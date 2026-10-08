-- Late PAPER FILL/PNL effects that complete pre-existing portfolio accounting
-- evidence must share the parent portfolio lock used by authoritative recovery.
-- The claimed-job trigger runs first, preserving the global job -> portfolio
-- lock order used by accounting and reconciliation.
CREATE OR REPLACE FUNCTION hope_lock_paper_portfolio_for_late_effect()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    parent_portfolio_id UUID;
BEGIN
    IF NEW.effect_type = 'FILL' THEN
        SELECT event.portfolio_id
          INTO parent_portfolio_id
          FROM paper_portfolio_pnl_events event
         WHERE event.fill_id = NEW.entity_id;
    ELSIF NEW.effect_type = 'PNL' THEN
        SELECT event.portfolio_id
          INTO parent_portfolio_id
          FROM paper_portfolio_pnl_events event
         WHERE event.pnl_event_id = NEW.entity_id;
    ELSE
        RETURN NEW;
    END IF;

    IF NOT FOUND THEN
        RETURN NEW;
    END IF;

    PERFORM 1
      FROM paper_portfolios
     WHERE portfolio_id = parent_portfolio_id
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'PAPER_EFFECT_PORTFOLIO_PARENT_NOT_FOUND'
            USING ERRCODE = '23503';
    END IF;

    RETURN NEW;
END;
$$;

-- Run after the claimed-job guard and before reciprocal payload/lineage checks.
CREATE TRIGGER trg_zzz_paper_effect_portfolio_parent_lock
BEFORE INSERT ON paper_effects
FOR EACH ROW
EXECUTE FUNCTION hope_lock_paper_portfolio_for_late_effect();
