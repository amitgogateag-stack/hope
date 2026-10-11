-- Legacy position-scoped P&L remains immutable recovery evidence.  Migration
-- 194 prevents a PNL effect from outliving both supported event families; close
-- the reciprocal legacy gap so a position P&L row cannot become durable without
-- the effect lineage that recovery uses to discover and authenticate it.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM pnl_events event
          LEFT JOIN paper_effects effect
            ON effect.effect_type = 'PNL'
           AND effect.entity_id = event.pnl_event_id
         WHERE effect.effect_id IS NULL
    ) THEN
        RAISE EXCEPTION 'PAPER_PNL_EVENT_WITHOUT_EFFECT'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_require_paper_pnl_event_effect()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- Preserve event-first/effect-first statement ordering inside one atomic
    -- transaction, while rejecting an immutable orphan at the commit boundary.
    IF NOT EXISTS (
        SELECT 1
          FROM paper_effects
         WHERE effect_type = 'PNL'
           AND entity_id = NEW.pnl_event_id
    ) THEN
        RAISE EXCEPTION 'PAPER_PNL_EVENT_WITHOUT_EFFECT'
            USING ERRCODE = '23514';
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_paper_pnl_event_effect_completeness
AFTER INSERT ON pnl_events
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION hope_require_paper_pnl_event_effect();
