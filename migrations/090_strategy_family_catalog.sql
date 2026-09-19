-- Canonical non-executable research catalog for HOPE strategy families.
CREATE TABLE strategy_family_catalog (
    family_code TEXT PRIMARY KEY,
    family_name TEXT NOT NULL UNIQUE,
    markets TEXT[] NOT NULL,
    regimes TEXT[] NOT NULL,
    priority TEXT NOT NULL CHECK (priority IN ('CORE','SECONDARY')),
    implementation_note TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (markets <@ ARRAY['INDIA','USA']::TEXT[]),
    CHECK (regimes <@ ARRAY['BULL_TREND','BEAR_TREND','SIDEWAYS','RECOVERY']::TEXT[])
);

INSERT INTO strategy_family_catalog(
    family_code,family_name,markets,regimes,priority,implementation_note
) VALUES
('CROSS_SECTIONAL_MOMENTUM','Cross-sectional momentum',ARRAY['INDIA','USA'],ARRAY['BULL_TREND','RECOVERY'],'CORE','Rank liquid stocks by medium-horizon relative strength; research long-only first.'),
('FIFTY_TWO_WEEK_HIGH','52-week-high momentum',ARRAY['INDIA','USA'],ARRAY['BULL_TREND','RECOVERY'],'CORE','Rank proximity to trailing 52-week high with liquidity and concentration controls.'),
('TIME_SERIES_TREND','Time-series trend',ARRAY['INDIA','USA'],ARRAY['BULL_TREND','BEAR_TREND'],'CORE','Trend family includes moving-average and breakout variants; long-flat first where short implementation is constrained.'),
('LOW_VOLATILITY_DEFENSIVE','Low-volatility defensive',ARRAY['INDIA','USA'],ARRAY['BEAR_TREND','SIDEWAYS'],'CORE','Prefer liquid lower-volatility equities; treat as defensive allocation, not a market-timing guarantee.'),
('QUALITY_DEFENSIVE','Quality defensive',ARRAY['INDIA','USA'],ARRAY['BEAR_TREND','SIDEWAYS','RECOVERY'],'CORE','Use profitability, leverage and earnings-stability signals with point-in-time fundamentals.'),
('MULTIFACTOR_QMV','Quality-momentum-low-volatility multifactor',ARRAY['INDIA','USA'],ARRAY['BULL_TREND','BEAR_TREND','SIDEWAYS','RECOVERY'],'CORE','Combine independently validated quality, momentum and low-volatility signals to reduce single-factor cyclicality.'),
('SECTOR_INDUSTRY_MOMENTUM','Sector and industry momentum',ARRAY['INDIA','USA'],ARRAY['BULL_TREND','RECOVERY'],'SECONDARY','Rank sectors or industries before stock selection; requires PIT industry classification.'),
('SHORT_TERM_REVERSAL','Short-term reversal',ARRAY['INDIA','USA'],ARRAY['SIDEWAYS','RECOVERY'],'SECONDARY','Restrict to liquidity-aware, non-panic conditions; explicitly stress spread, impact and turnover.'),
('EARNINGS_DRIFT','Post-earnings announcement drift',ARRAY['USA'],ARRAY['BULL_TREND','BEAR_TREND','SIDEWAYS','RECOVERY'],'SECONDARY','USA-first event strategy requiring PIT earnings surprise and announcement timestamps.'),
('VALUE_RECOVERY','Value and recovery',ARRAY['INDIA','USA'],ARRAY['RECOVERY','SIDEWAYS'],'SECONDARY','Long-horizon research family; pair valuation with quality controls to reduce value-trap exposure.');

CREATE OR REPLACE FUNCTION prevent_strategy_family_catalog_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'STRATEGY_FAMILY_CATALOG_IMMUTABLE' USING ERRCODE='23514';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_strategy_family_catalog_immutable
BEFORE UPDATE OR DELETE ON strategy_family_catalog
FOR EACH ROW EXECUTE FUNCTION prevent_strategy_family_catalog_mutation();
