-- Preserve provider-symbol identity lineage at the database finalization
-- boundary. One source symbol cannot identify different canonical instruments
-- across windows in the same immutable manifest.

CREATE OR REPLACE FUNCTION hope_guard_market_data_manifest_identity_continuity()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    manifest_doc JSONB;
    conflicting_bindings BIGINT;
BEGIN
    IF OLD.immutable IS TRUE OR NEW.immutable IS NOT TRUE THEN
        RETURN NEW;
    END IF;

    SELECT manifest INTO manifest_doc
    FROM market_data_coverage_manifests
    WHERE dataset_version_id = NEW.dataset_version_id;
    IF manifest_doc IS NULL THEN
        RETURN NEW;
    END IF;

    WITH bindings AS (
        SELECT
            w->>'source' AS source,
            i->>'source_symbol' AS source_symbol,
            (i->>'instrument_id')::uuid AS instrument_id
        FROM jsonb_array_elements(manifest_doc->'windows') AS w
        CROSS JOIN LATERAL jsonb_array_elements(w->'instruments') AS i
    )
    SELECT count(*) INTO conflicting_bindings
    FROM (
        SELECT source, source_symbol
        FROM bindings
        GROUP BY source, source_symbol
        HAVING count(DISTINCT instrument_id) > 1
    ) conflicts;

    IF conflicting_bindings <> 0 THEN
        RAISE EXCEPTION 'MARKET_DATA_FINALIZATION_IDENTITY_BINDING_CONFLICT'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_market_data_manifest_identity_continuity ON dataset_versions;
CREATE TRIGGER trg_market_data_manifest_identity_continuity
BEFORE UPDATE ON dataset_versions
FOR EACH ROW EXECUTE FUNCTION hope_guard_market_data_manifest_identity_continuity();
