-- Reject manifest shapes that the authoritative Python parser would refuse.
-- In particular, an empty instrument list must not be silently ignored while
-- other valid windows provide enough rows for exact-coverage sealing.

CREATE OR REPLACE FUNCTION hope_guard_market_data_manifest_structure()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    manifest_doc JSONB;
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

    IF jsonb_typeof(manifest_doc) <> 'object'
       OR jsonb_typeof(manifest_doc->'windows') <> 'array'
       OR jsonb_array_length(manifest_doc->'windows') = 0 THEN
        RAISE EXCEPTION 'MARKET_DATA_FINALIZATION_MANIFEST_STRUCTURE_INVALID'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM jsonb_array_elements(manifest_doc->'windows') AS w
        WHERE jsonb_typeof(w) <> 'object'
           OR jsonb_typeof(w->'instruments') <> 'array'
    ) THEN
        RAISE EXCEPTION 'MARKET_DATA_FINALIZATION_MANIFEST_STRUCTURE_INVALID'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM jsonb_array_elements(manifest_doc->'windows') AS w
        WHERE jsonb_array_length(w->'instruments') = 0
           OR EXISTS (
                SELECT 1
                FROM jsonb_array_elements(w->'instruments') AS i
                WHERE jsonb_typeof(i) <> 'object'
           )
    ) THEN
        RAISE EXCEPTION 'MARKET_DATA_FINALIZATION_MANIFEST_STRUCTURE_INVALID'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_market_data_manifest_structure ON dataset_versions;
CREATE TRIGGER trg_market_data_manifest_structure
BEFORE UPDATE ON dataset_versions
FOR EACH ROW EXECUTE FUNCTION hope_guard_market_data_manifest_structure();
