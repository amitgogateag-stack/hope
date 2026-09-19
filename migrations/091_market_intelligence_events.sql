-- Provider-neutral, immutable market/company intelligence evidence.
CREATE TABLE market_intelligence_events (
    event_id UUID PRIMARY KEY,
    scope TEXT NOT NULL CHECK (scope IN ('COMPANY','MARKET')),
    instrument_id UUID REFERENCES instruments(instrument_id),
    universe_version_id UUID REFERENCES universe_versions(universe_version_id),
    source TEXT NOT NULL CHECK (btrim(source) <> '' AND source = btrim(source)),
    source_item_id TEXT NOT NULL CHECK (
        btrim(source_item_id) <> '' AND source_item_id = btrim(source_item_id)
    ),
    source_tier SMALLINT NOT NULL CHECK (source_tier BETWEEN 1 AND 4),
    category TEXT NOT NULL CHECK (category IN (
        'EARNINGS','GUIDANCE','CORPORATE_ACTION','CAPITAL_RAISE',
        'MERGER_ACQUISITION','MANAGEMENT_CHANGE','REGULATORY','CREDIT',
        'CONTRACT','LITIGATION','INSOLVENCY','OWNERSHIP','TRADING_STATUS',
        'MACRO','GEOPOLITICAL','OTHER'
    )),
    materiality TEXT NOT NULL CHECK (materiality IN ('LOW','MEDIUM','HIGH','CRITICAL')),
    recommended_action TEXT NOT NULL CHECK (recommended_action IN (
        'NO_ACTION','OBSERVE','BLOCK_NEW_ENTRY','REDUCE_RISK_CANDIDATE',
        'EXIT_CANDIDATE','DATA_REVIEW_REQUIRED','MARKET_RISK_HALT_CANDIDATE'
    )),
    event_time TIMESTAMPTZ NOT NULL,
    available_time TIMESTAMPTZ NOT NULL,
    ingestion_time TIMESTAMPTZ NOT NULL,
    source_payload_hash CHAR(64) NOT NULL CHECK (
        source_payload_hash ~ '^[0-9a-f]{64}$'
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(source, source_item_id),
    CHECK (available_time >= event_time),
    CHECK (ingestion_time >= available_time),
    CHECK (
        (scope = 'COMPANY' AND instrument_id IS NOT NULL)
        OR (scope = 'MARKET' AND instrument_id IS NULL)
    ),
    CHECK (
        source_tier <> 4
        OR recommended_action IN ('NO_ACTION','OBSERVE','DATA_REVIEW_REQUIRED')
    )
);

CREATE OR REPLACE FUNCTION guard_market_intelligence_universe_link()
RETURNS trigger AS $$
BEGIN
    IF NEW.scope = 'COMPANY' AND NEW.universe_version_id IS NOT NULL AND NOT EXISTS (
        SELECT 1
        FROM universe_members um
        WHERE um.universe_version_id = NEW.universe_version_id
          AND um.instrument_id = NEW.instrument_id
          AND (um.valid_from IS NULL OR NEW.available_time >= um.valid_from)
          AND (um.valid_to IS NULL OR NEW.available_time < um.valid_to)
    ) THEN
        RAISE EXCEPTION 'INTELLIGENCE_INSTRUMENT_NOT_IN_PIT_UNIVERSE'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_market_intelligence_universe_link
BEFORE INSERT ON market_intelligence_events
FOR EACH ROW EXECUTE FUNCTION guard_market_intelligence_universe_link();

CREATE OR REPLACE FUNCTION prevent_market_intelligence_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'MARKET_INTELLIGENCE_EVENT_IMMUTABLE' USING ERRCODE='23514';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_market_intelligence_immutable
BEFORE UPDATE OR DELETE ON market_intelligence_events
FOR EACH ROW EXECUTE FUNCTION prevent_market_intelligence_mutation();
