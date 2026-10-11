-- A PNL effect is a claim that a durable portfolio accounting event exists.
-- The previous event-side constraint enforced event -> effects, but did not
-- prevent a PNL effect with no corresponding event from committing. Keep the
-- legitimate effect-first/event-first order inside one atomic transaction.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM paper_effects effect
          LEFT JOIN paper_portfolio_pnl_events event
            ON event.pnl_event_id = effect.entity_id
         WHERE effect.effect_type = 'PNL'
           AND event.pnl_event_id IS NULL
    ) THEN
        RAISE EXCEPTION 'PAPER_PNL_EFFECT_WITHOUT_PORTFOLIO_EVENT'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_require_paper_pnl_effect_event()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.effect_type <> 'PNL' THEN
        RETURN NULL;
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM paper_portfolio_pnl_events
         WHERE pnl_event_id = NEW.entity_id
    ) THEN
        RAISE EXCEPTION 'PAPER_PNL_EFFECT_WITHOUT_PORTFOLIO_EVENT'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_paper_pnl_effect_event_completeness
AFTER INSERT ON paper_effects
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION hope_require_paper_pnl_effect_event();
