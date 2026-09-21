-- Keep durable identity mappings aligned with the authoritative domain contract.
-- Identity resolution is fail-closed, so malformed rows must not be admitted through
-- direct SQL and later become ambiguous or unusable at the repository boundary.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM identity_mappings
        WHERE source_symbol = ''
           OR source_symbol <> btrim(source_symbol)
           OR broker_instrument_id = ''
           OR broker_instrument_id <> btrim(broker_instrument_id)
           OR (reason IS NOT NULL AND (reason = '' OR reason <> btrim(reason)))
           OR (status = 'ACTIVE' AND canonical_instrument_id IS NULL)
           OR (status = 'TERMINAL' AND canonical_instrument_id IS NOT NULL)
           OR (status <> 'ACTIVE' AND reason IS NULL)
    ) THEN
        RAISE EXCEPTION 'IDENTITY_MAPPING_CONTRACT_VIOLATION'
            USING ERRCODE='23514';
    END IF;
END;
$$;

ALTER TABLE identity_mappings
    ADD CONSTRAINT ck_identity_mapping_source_symbol_canonical
        CHECK (source_symbol <> '' AND source_symbol = btrim(source_symbol)),
    ADD CONSTRAINT ck_identity_mapping_broker_id_canonical
        CHECK (broker_instrument_id <> '' AND broker_instrument_id = btrim(broker_instrument_id)),
    ADD CONSTRAINT ck_identity_mapping_reason_canonical
        CHECK (reason IS NULL OR (reason <> '' AND reason = btrim(reason))),
    ADD CONSTRAINT ck_identity_mapping_active_binding
        CHECK (status <> 'ACTIVE' OR canonical_instrument_id IS NOT NULL),
    ADD CONSTRAINT ck_identity_mapping_terminal_binding
        CHECK (status <> 'TERMINAL' OR canonical_instrument_id IS NULL),
    ADD CONSTRAINT ck_identity_mapping_nonactive_reason
        CHECK (status = 'ACTIVE' OR reason IS NOT NULL);
