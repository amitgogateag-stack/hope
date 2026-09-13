-- Durable fill economics must remain finite execution-domain values.
-- PostgreSQL NUMERIC accepts NaN/Infinity, so positivity/non-negativity alone is insufficient.
ALTER TABLE fills
    ADD CONSTRAINT ck_fills_fill_price_finite
        CHECK (fill_price::text NOT IN ('NaN', 'Infinity', '-Infinity')),
    ADD CONSTRAINT ck_fills_slippage_finite
        CHECK (slippage::text NOT IN ('NaN', 'Infinity', '-Infinity')),
    ADD CONSTRAINT ck_fills_transaction_cost_finite
        CHECK (transaction_cost::text NOT IN ('NaN', 'Infinity', '-Infinity'));
