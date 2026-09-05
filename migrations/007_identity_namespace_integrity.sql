-- HOPE v0.1 identity namespace integrity.
-- A canonical instrument may legitimately have one active broker identity per broker.
-- The prior instrument-wide uniqueness guard was too restrictive for multi-broker operation.

DROP INDEX IF EXISTS uq_broker_active_instrument;

CREATE UNIQUE INDEX IF NOT EXISTS uq_broker_active_canonical_per_broker
    ON broker_instruments (broker, instrument_id)
    WHERE status = 'ACTIVE' AND instrument_id IS NOT NULL;

-- source_symbol is not globally unique across brokers/sources. The broker identity
-- supplies the namespace until a first-class source namespace is introduced.
DROP INDEX IF EXISTS uq_identity_active_source_symbol;

CREATE UNIQUE INDEX IF NOT EXISTS uq_identity_active_source_symbol_per_broker
    ON identity_mappings (broker_instrument_id, source_symbol)
    WHERE status = 'ACTIVE';
