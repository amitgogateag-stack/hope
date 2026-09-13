-- Restart-safe PAPER portfolio state must never persist non-finite cash.
-- Negative finite cash remains valid because execution/accounting can legitimately
-- move cash below zero; this guard only mirrors PortfolioLedger.from_state.
ALTER TABLE paper_portfolios
    ADD CONSTRAINT ck_paper_portfolios_cash_finite
    CHECK (
        cash <> 'NaN'::numeric
        AND cash <> 'Infinity'::numeric
        AND cash <> '-Infinity'::numeric
    );
