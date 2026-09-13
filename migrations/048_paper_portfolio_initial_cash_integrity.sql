-- Authoritative PAPER portfolios must start from finite, non-negative capital.
ALTER TABLE paper_portfolios
    ADD CONSTRAINT ck_paper_portfolios_initial_cash_finite_nonnegative
    CHECK (
        initial_cash >= 0
        AND initial_cash <> 'NaN'::numeric
        AND initial_cash <> 'Infinity'::numeric
    );
