-- A canonical PAPER P&L position must originate from an actionable SIGNAL.
-- Existing position FKs and migration 197 authenticate identity/effect ownership
-- but do not reject a NO_SIGNAL or STALE_SIGNAL source decision. Those are not
-- valid execution instructions. Preserve nullable pre-168 legacy compatibility.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pnl_events event
        JOIN positions position ON position.position_id = event.position_id
        JOIN signals signal ON signal.signal_id = position.opened_from_signal_id
        WHERE (
            signal.strategy_version IS NOT NULL
            OR signal.signal_type IS NOT NULL
            OR signal.conviction IS NOT NULL
            OR signal.inputs_hash IS NOT NULL
        ) AND signal.state <> 'SIGNAL'
    ) THEN
        RAISE EXCEPTION 'PAPER_PNL_CANONICAL_SOURCE_SIGNAL_NOT_ACTIONABLE'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_require_actionable_canonical_pnl_source()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM positions position
        JOIN signals signal ON signal.signal_id = position.opened_from_signal_id
        WHERE position.position_id = NEW.position_id
          AND (
              signal.strategy_version IS NOT NULL
              OR signal.signal_type IS NOT NULL
              OR signal.conviction IS NOT NULL
              OR signal.inputs_hash IS NOT NULL
          ) AND signal.state <> 'SIGNAL'
    ) THEN
        RAISE EXCEPTION 'PAPER_PNL_CANONICAL_SOURCE_SIGNAL_NOT_ACTIONABLE'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_paper_pnl_actionable_canonical_source
AFTER INSERT ON pnl_events
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION hope_require_actionable_canonical_pnl_source();
