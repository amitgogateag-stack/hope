-- Provider identity in immutable market-data evidence must be present and
-- canonical even when finalization is attempted directly in SQL.

CREATE OR REPLACE FUNCTION hope_guard_market_data_manifest_identity_canonical()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    manifest_doc JSONB;
    dataset_source TEXT;
BEGIN
    IF OLD.immutable IS TRUE OR NEW.immutable IS NOT TRUE THEN
        RETURN NEW;
    END IF;

    SELECT m.manifest, d.source
    INTO manifest_doc, dataset_source
    FROM market_data_coverage_manifests m
    JOIN datasets d ON d.dataset_id = NEW.dataset_id
    WHERE m.dataset_version_id = NEW.dataset_version_id;
    IF manifest_doc IS NULL THEN
        RETURN NEW;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM jsonb_array_elements(manifest_doc->'windows') AS w
        WHERE w->>'source' IS NULL
           OR w->>'source' = ''
           OR w->>'source' <> btrim(w->>'source')
           OR w->>'source' <> dataset_source
    ) OR EXISTS (
        SELECT 1
        FROM jsonb_array_elements(manifest_doc->'windows') AS w
        CROSS JOIN LATERAL jsonb_array_elements(w->'instruments') AS i
        WHERE i->>'source_symbol' IS NULL
           OR i->>'source_symbol' = ''
           OR i->>'source_symbol' <> btrim(i->>'source_symbol')
    ) THEN
        RAISE EXCEPTION 'MARKET_DATA_FINALIZATION_IDENTITY_NOT_CANONICAL'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_market_data_manifest_identity_canonical ON dataset_versions;
CREATE TRIGGER trg_market_data_manifest_identity_canonical
BEFORE UPDATE ON dataset_versions
FOR EACH ROW EXECUTE FUNCTION hope_guard_market_data_manifest_identity_canonical();
