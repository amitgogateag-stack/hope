-- HOPE v0.1 initial PostgreSQL schema.
-- This SQL is intentionally explicit and append-only for research history.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS instruments (
    instrument_id UUID PRIMARY KEY,
    canonical_symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE','TERMINAL','DUPLICATE','AMBIGUOUS','UNRESOLVED','SUPERSEDED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS broker_instruments (
    broker_instrument_id TEXT PRIMARY KEY,
    broker TEXT NOT NULL,
    instrument_id UUID REFERENCES instruments(instrument_id),
    status TEXT NOT NULL CHECK (status IN ('ACTIVE','TERMINAL','DUPLICATE','AMBIGUOUS','UNRESOLVED','SUPERSEDED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS identity_mappings (
    identity_mapping_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_symbol TEXT NOT NULL,
    broker_instrument_id TEXT NOT NULL REFERENCES broker_instruments(broker_instrument_id),
    canonical_instrument_id UUID REFERENCES instruments(instrument_id),
    status TEXT NOT NULL CHECK (status IN ('ACTIVE','TERMINAL','DUPLICATE','AMBIGUOUS','UNRESOLVED','SUPERSEDED')),
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS datasets (
    dataset_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    source TEXT NOT NULL,
    pit_certified BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dataset_versions (
    dataset_version_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id UUID NOT NULL REFERENCES datasets(dataset_id),
    version TEXT NOT NULL,
    vintage_label TEXT NOT NULL,
    immutable BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(dataset_id, version)
);

CREATE TABLE IF NOT EXISTS universes (
    universe_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS universe_versions (
    universe_version_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    universe_id UUID NOT NULL REFERENCES universes(universe_id),
    version TEXT NOT NULL,
    pit_certified BOOLEAN NOT NULL DEFAULT FALSE,
    declared_member_count INTEGER NOT NULL CHECK (declared_member_count >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(universe_id, version)
);

CREATE TABLE IF NOT EXISTS universe_members (
    universe_version_id UUID NOT NULL REFERENCES universe_versions(universe_version_id),
    instrument_id UUID NOT NULL REFERENCES instruments(instrument_id),
    valid_from TIMESTAMPTZ,
    valid_to TIMESTAMPTZ,
    PRIMARY KEY (universe_version_id, instrument_id)
);

CREATE TABLE IF NOT EXISTS strategies (
    strategy_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    family TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS strategy_versions (
    strategy_version_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_id UUID NOT NULL REFERENCES strategies(strategy_id),
    version TEXT NOT NULL,
    code_commit TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(strategy_id, version)
);

CREATE TABLE IF NOT EXISTS configuration_snapshots (
    configuration_hash CHAR(64) PRIMARY KEY CHECK (configuration_hash ~ '^[0-9a-f]{64}$'),
    canonical_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS experiments (
    experiment_id TEXT PRIMARY KEY,
    hypothesis TEXT NOT NULL,
    strategy_version_id UUID NOT NULL REFERENCES strategy_versions(strategy_version_id),
    dataset_version_id UUID NOT NULL REFERENCES dataset_versions(dataset_version_id),
    universe_version_id UUID NOT NULL REFERENCES universe_versions(universe_version_id),
    configuration_hash CHAR(64) NOT NULL REFERENCES configuration_snapshots(configuration_hash),
    environment TEXT NOT NULL CHECK (environment IN ('RESEARCH','BACKTEST','WALK_FORWARD','PAPER')),
    status TEXT NOT NULL DEFAULT 'CREATED',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS signals (
    signal_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id TEXT REFERENCES experiments(experiment_id),
    instrument_id UUID NOT NULL REFERENCES instruments(instrument_id),
    decision_time TIMESTAMPTZ NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('NO_SIGNAL','SIGNAL','STALE_SIGNAL')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS orders (
    order_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id UUID NOT NULL REFERENCES signals(signal_id),
    instrument_id UUID NOT NULL REFERENCES instruments(instrument_id),
    environment TEXT NOT NULL CHECK (environment IN ('RESEARCH','BACKTEST','WALK_FORWARD','PAPER')),
    side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
    quantity NUMERIC NOT NULL CHECK (quantity > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fills (
    fill_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID NOT NULL REFERENCES orders(order_id),
    quantity NUMERIC NOT NULL CHECK (quantity > 0),
    fill_price NUMERIC NOT NULL CHECK (fill_price > 0),
    slippage NUMERIC NOT NULL DEFAULT 0,
    transaction_cost NUMERIC NOT NULL DEFAULT 0,
    filled_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    position_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    instrument_id UUID NOT NULL REFERENCES instruments(instrument_id),
    opened_from_signal_id UUID REFERENCES signals(signal_id),
    quantity NUMERIC NOT NULL,
    opened_at TIMESTAMPTZ NOT NULL,
    closed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS pnl_events (
    pnl_event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    position_id UUID NOT NULL REFERENCES positions(position_id),
    amount NUMERIC NOT NULL,
    event_time TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_events (
    audit_event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
