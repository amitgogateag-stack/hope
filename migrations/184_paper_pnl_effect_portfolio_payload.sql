-- Preserve the portfolio PNL payload contract when legacy accounting evidence
-- exists before its effect.  Migration 183 guards PNL-row insertion; this
-- reciprocal guard makes the invariant independent of insertion order.
CREATE OR REPLACE FUNCTION hope_guard_paper_pnl_effect_portfolio_payload()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    expected_payload_hash TEXT;
BEGIN
    IF NEW.effect_type <> 'PNL' THEN
        RETURN NEW;
    END IF;

    SELECT hope_paper_portfolio_pnl_payload_hash(
               event.pnl_event_id,
               event.portfolio_id,
               event.fill_id,
               event.instrument_id,
               event.realized_pnl_delta,
               event.commission_delta,
               event.event_time
           )
      INTO expected_payload_hash
      FROM paper_portfolio_pnl_events event
     WHERE event.pnl_event_id = NEW.entity_id;

    IF NOT FOUND THEN
        RETURN NEW;
    END IF;

    IF NEW.payload_hash <> expected_payload_hash THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_PNL_EFFECT_PAYLOAD_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

-- Run after canonical effect identity and claimed-job serialization guards.
CREATE TRIGGER trg_zzzz_paper_effect_pnl_portfolio_payload
BEFORE INSERT ON paper_effects
FOR EACH ROW
EXECUTE FUNCTION hope_guard_paper_pnl_effect_portfolio_payload();
