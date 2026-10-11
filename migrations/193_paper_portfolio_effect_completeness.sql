-- A materialized portfolio accounting transition is not durable truth unless
-- both its source fill and derived P&L are represented in the PAPER effect
-- ledger.  Earlier row-side guards authenticated effects when present, but
-- deliberately allowed a legacy event with no effects to commit.  Close that
-- recovery gap at the transaction boundary while preserving effect-first or
-- event-first statement ordering inside one transaction.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM paper_portfolio_pnl_events event
          LEFT JOIN paper_effects effect
            ON effect.effect_type = 'FILL'
           AND effect.entity_id = event.fill_id
         WHERE effect.effect_id IS NULL
    ) THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_SOURCE_FILL_EFFECT_MISSING'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM paper_portfolio_pnl_events event
          LEFT JOIN paper_effects effect
            ON effect.effect_type = 'PNL'
           AND effect.entity_id = event.pnl_event_id
         WHERE effect.effect_id IS NULL
    ) THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_PNL_EFFECT_MISSING'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_require_paper_portfolio_effect_completeness()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- A later statement may have removed the row through transaction rollback;
    -- do not require effects for an event that will not become durable.
    IF NOT EXISTS (
        SELECT 1
          FROM paper_portfolio_pnl_events
         WHERE pnl_event_id = NEW.pnl_event_id
    ) THEN
        RETURN NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM paper_effects
         WHERE effect_type = 'FILL'
           AND entity_id = NEW.fill_id
    ) THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_SOURCE_FILL_EFFECT_MISSING'
            USING ERRCODE = '23514';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM paper_effects
         WHERE effect_type = 'PNL'
           AND entity_id = NEW.pnl_event_id
    ) THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_PNL_EFFECT_MISSING'
            USING ERRCODE = '23514';
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_paper_portfolio_effect_completeness
AFTER INSERT ON paper_portfolio_pnl_events
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION hope_require_paper_portfolio_effect_completeness();
