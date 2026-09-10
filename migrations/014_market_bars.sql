CREATE TABLE IF NOT EXISTS market_bars (
    market_bar_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_version_id UUID NOT NULL REFERENCES dataset_versions(dataset_version_id),
    instrument_id UUID NOT NULL REFERENCES instruments(instrument_id),
    event_time TIMESTAMPTZ NOT NULL,
    available_time TIMESTAMPTZ NOT NULL,
    effective_time TIMESTAMPTZ,
    ingestion_time TIMESTAMPTZ NOT NULL,
    open NUMERIC NOT NULL CHECK (open > 0),
    high NUMERIC NOT NULL CHECK (high > 0),
    low NUMERIC NOT NULL CHECK (low > 0),
    close NUMERIC NOT NULL CHECK (close > 0),
    volume NUMERIC NOT NULL CHECK (volume >= 0),
    CHECK (available_time >= event_time),
    CHECK (ingestion_time >= available_time),
    CHECK (high >= GREATEST(open, close)),
    CHECK (low <= LEAST(open, close)),
    CHECK (high >= low),
    UNIQUE(dataset_version_id, instrument_id, event_time, available_time, ingestion_time)
);

CREATE INDEX IF NOT EXISTS ix_market_bars_pit_lookup
    ON market_bars(dataset_version_id, instrument_id, event_time, available_time, ingestion_time);
