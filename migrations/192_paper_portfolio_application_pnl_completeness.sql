-- Every durable fill application must have exactly one authoritative P&L
-- event.  The event already references the application; this reciprocal,
-- deferred foreign key prevents the application half from committing alone
-- while preserving the repository's application-first statement order.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM paper_portfolio_fill_applications application
          LEFT JOIN paper_portfolio_pnl_events event
            ON event.portfolio_id = application.portfolio_id
           AND event.fill_id = application.fill_id
         WHERE event.pnl_event_id IS NULL
    ) THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_APPLICATION_PNL_MISSING'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

ALTER TABLE paper_portfolio_fill_applications
    ADD CONSTRAINT fk_paper_portfolio_application_pnl
    FOREIGN KEY (portfolio_id, fill_id)
    REFERENCES paper_portfolio_pnl_events(portfolio_id, fill_id)
    DEFERRABLE INITIALLY DEFERRED;
