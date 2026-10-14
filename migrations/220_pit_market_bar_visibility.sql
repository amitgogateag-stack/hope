-- Centralize point-in-time market-bar visibility in PostgreSQL so every
-- production reader shares the same event, availability, ingestion, and
-- effective-time boundary. Future-effective evidence may remain durably stored
-- in a sealed dataset, but it cannot enter a decision before its effective time.

CREATE OR REPLACE FUNCTION hope_pit_market_bars(
    p_dataset_version_id UUID,
    p_as_of TIMESTAMPTZ
)
RETURNS TABLE (
    instrument_id UUID,
    event_time TIMESTAMPTZ,
    available_time TIMESTAMPTZ,
    effective_time TIMESTAMPTZ,
    ingestion_time TIMESTAMPTZ,
    open NUMERIC,
    high NUMERIC,
    low NUMERIC,
    close NUMERIC,
    volume NUMERIC
)
LANGUAGE sql STABLE
AS $$
    SELECT
        bar.instrument_id,
        bar.event_time,
        bar.available_time,
        bar.effective_time,
        bar.ingestion_time,
        bar.open,
        bar.high,
        bar.low,
        bar.close,
        bar.volume
    FROM market_bars AS bar
    WHERE p_as_of IS NOT NULL
      AND bar.dataset_version_id = p_dataset_version_id
      AND bar.event_time <= p_as_of
      AND bar.available_time <= p_as_of
      AND bar.ingestion_time <= p_as_of
      AND (bar.effective_time IS NULL OR bar.effective_time <= p_as_of)
$$;
