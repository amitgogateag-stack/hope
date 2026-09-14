-- Durable intended market-data coverage manifest.
-- One canonical immutable declaration per staging dataset version. The finalizer
-- must prove persisted market_bars against this declaration rather than against
-- whatever request subset happens to be supplied at seal time.

CREATE TABLE IF NOT EXISTS market_data_coverage_manifests (
    dataset_version_id UUID PRIMARY KEY REFERENCES dataset_versions(dataset_version_id),
    manifest_hash CHAR(64) NOT NULL CHECK (manifest_hash ~ '^[0-9a-f]{64}$'),
    manifest JSONB NOT NULL,
    declared_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (jsonb_typeof(manifest) = 'object')
);

CREATE OR REPLACE FUNCTION hope_guard_market_data_coverage_manifest()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    version_immutable BOOLEAN;
    existing_bars BIGINT;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT immutable INTO version_immutable
        FROM dataset_versions
        WHERE dataset_version_id = NEW.dataset_version_id;

        IF version_immutable IS NULL THEN
            RAISE EXCEPTION 'MARKET_DATA_MANIFEST_DATASET_VERSION_NOT_FOUND: %%', NEW.dataset_version_id
                USING ERRCODE = '23514';
        END IF;
        IF version_immutable IS TRUE THEN
            RAISE EXCEPTION 'MARKET_DATA_MANIFEST_IMMUTABLE_DATASET_VERSION: %%', NEW.dataset_version_id
                USING ERRCODE = '23514';
        END IF;

        SELECT count(*) INTO existing_bars
        FROM market_bars
        WHERE dataset_version_id = NEW.dataset_version_id;
        IF existing_bars > 0 THEN
            RAISE EXCEPTION 'MARKET_DATA_MANIFEST_REQUIRES_EMPTY_STAGING_VERSION: %%', NEW.dataset_version_id
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    RAISE EXCEPTION 'MARKET_DATA_MANIFEST_IMMUTABLE: coverage declaration is append-only'
        USING ERRCODE = '23514';
END;
$$;

DROP TRIGGER IF EXISTS trg_market_data_coverage_manifest_immutable
    ON market_data_coverage_manifests;
CREATE TRIGGER trg_market_data_coverage_manifest_immutable
BEFORE INSERT OR UPDATE OR DELETE ON market_data_coverage_manifests
FOR EACH ROW EXECUTE FUNCTION hope_guard_market_data_coverage_manifest();
