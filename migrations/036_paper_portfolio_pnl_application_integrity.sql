-- Authoritative PAPER P&L must correspond to a durably applied portfolio fill.
ALTER TABLE paper_portfolio_pnl_events
    ADD CONSTRAINT fk_paper_portfolio_pnl_applied_fill
    FOREIGN KEY (portfolio_id, fill_id)
    REFERENCES paper_portfolio_fill_applications(portfolio_id, fill_id);
