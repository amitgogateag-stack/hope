-- Market-data numerics must remain finite before they can enter PIT research/execution paths.
-- PostgreSQL NUMERIC accepts NaN/Infinity, so sign checks alone are insufficient.
ALTER TABLE market_bars
    ADD CONSTRAINT ck_market_bars_open_finite
        CHECK (open::text NOT IN ('NaN', 'Infinity', '-Infinity')),
    ADD CONSTRAINT ck_market_bars_high_finite
        CHECK (high::text NOT IN ('NaN', 'Infinity', '-Infinity')),
    ADD CONSTRAINT ck_market_bars_low_finite
        CHECK (low::text NOT IN ('NaN', 'Infinity', '-Infinity')),
    ADD CONSTRAINT ck_market_bars_close_finite
        CHECK (close::text NOT IN ('NaN', 'Infinity', '-Infinity')),
    ADD CONSTRAINT ck_market_bars_volume_finite
        CHECK (volume::text NOT IN ('NaN', 'Infinity', '-Infinity'));
