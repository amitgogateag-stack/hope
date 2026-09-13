-- Authoritative PAPER realized-P&L deltas are finite accounting values.
-- PostgreSQL NUMERIC accepts NaN/Infinity, so the application contract must also hold at storage.
ALTER TABLE paper_portfolio_pnl_events
    ADD CONSTRAINT ck_paper_portfolio_pnl_realized_delta_finite
    CHECK (realized_pnl_delta::text NOT IN ('NaN', 'Infinity', '-Infinity'));
