-- Restart-safe PAPER positions require finite numeric state matching PortfolioLedger.from_state().
ALTER TABLE paper_portfolio_positions
    ADD CONSTRAINT ck_paper_portfolio_positions_quantity_finite
        CHECK (quantity NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    ADD CONSTRAINT ck_paper_portfolio_positions_average_price_finite
        CHECK (average_price NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    ADD CONSTRAINT ck_paper_portfolio_positions_realized_pnl_finite
        CHECK (realized_pnl NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    ADD CONSTRAINT ck_paper_portfolio_positions_total_commission_finite
        CHECK (total_commission NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric));
