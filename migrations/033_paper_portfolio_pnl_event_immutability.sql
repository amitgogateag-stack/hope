-- Preserve authoritative PAPER accounting history as append-only evidence.
CREATE OR REPLACE FUNCTION hope_reject_paper_portfolio_pnl_event_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'PAPER_PORTFOLIO_PNL_EVENT_IMMUTABLE: accounting events cannot be modified or deleted'
        USING ERRCODE = '23514';
END;
$$;

DROP TRIGGER IF EXISTS trg_paper_portfolio_pnl_event_immutable ON paper_portfolio_pnl_events;
CREATE TRIGGER trg_paper_portfolio_pnl_event_immutable
BEFORE UPDATE OR DELETE ON paper_portfolio_pnl_events
FOR EACH ROW EXECUTE FUNCTION hope_reject_paper_portfolio_pnl_event_mutation();
