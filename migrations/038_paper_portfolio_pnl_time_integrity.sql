-- Authoritative PAPER P&L event time must equal the source fill time.
CREATE OR REPLACE FUNCTION hope_guard_paper_portfolio_pnl_event_time()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    source_fill_time TIMESTAMPTZ;
BEGIN
    SELECT filled_at INTO source_fill_time
    FROM fills
    WHERE fill_id = NEW.fill_id;

    IF source_fill_time IS DISTINCT FROM NEW.event_time THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = concat(
                'PAPER_PORTFOLIO_PNL_EVENT_TIME_MISMATCH: fill ', NEW.fill_id,
                ' filled_at ', source_fill_time,
                ' vs pnl event_time ', NEW.event_time
            );
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_portfolio_pnl_event_time_integrity
BEFORE INSERT ON paper_portfolio_pnl_events
FOR EACH ROW
EXECUTE FUNCTION hope_guard_paper_portfolio_pnl_event_time();
