-- Prevent market-data dataset versions from being marked sealed without durable,
-- complete manifest-backed evidence. This is intentionally scoped to versions
-- that have market bars or a market-data coverage manifest so generic dataset
-- versions retain their existing lifecycle.

CREATE OR REPLACE FUNCTION hope_guard_market_data_version_finalization()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    manifest_doc JSONB;
    manifest_universe UUID;
    dataset_source TEXT;
    dataset_pit BOOLEAN;
    universe_pit BOOLEAN;
    declared_members INTEGER;
    actual_members INTEGER;
    expected_count BIGINT;
    actual_count BIGINT;
    actual_distinct_count BIGINT;
    missing_count BIGINT;
    extra_count BIGINT;
BEGIN
    IF TG_OP <> 'UPDATE' OR OLD.immutable IS TRUE OR NEW.immutable IS NOT TRUE THEN
        RETURN NEW;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM market_data_coverage_manifests
        WHERE dataset_version_id = NEW.dataset_version_id
    ) AND NOT EXISTS (
        SELECT 1 FROM market_bars
        WHERE dataset_version_id = NEW.dataset_version_id
    ) THEN
        RETURN NEW;
    END IF;

    IF NEW.vintage_label <> 'sealed' THEN
        RAISE EXCEPTION 'MARKET_DATA_FINALIZATION_REQUIRES_SEALED_LABEL'
            USING ERRCODE = '23514';
    END IF;

    SELECT m.manifest, m.universe_version_id, d.source, d.pit_certified
    INTO manifest_doc, manifest_universe, dataset_source, dataset_pit
    FROM market_data_coverage_manifests m
    JOIN datasets d ON d.dataset_id = NEW.dataset_id
    WHERE m.dataset_version_id = NEW.dataset_version_id;

    IF manifest_doc IS NULL THEN
        RAISE EXCEPTION 'MARKET_DATA_FINALIZATION_REQUIRES_COVERAGE_MANIFEST'
            USING ERRCODE = '23514';
    END IF;
    IF dataset_pit IS NOT TRUE THEN
        RAISE EXCEPTION 'MARKET_DATA_FINALIZATION_REQUIRES_PIT_DATASET'
            USING ERRCODE = '23514';
    END IF;
    IF manifest_doc->>'version' <> '2'
       OR manifest_doc->>'universe_version_id' <> manifest_universe::text THEN
        RAISE EXCEPTION 'MARKET_DATA_FINALIZATION_MANIFEST_INVALID'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM jsonb_array_elements(manifest_doc->'windows') AS w
        WHERE w->>'source' <> dataset_source
    ) THEN
        RAISE EXCEPTION 'MARKET_DATA_FINALIZATION_SOURCE_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    SELECT pit_certified, declared_member_count
    INTO universe_pit, declared_members
    FROM universe_versions
    WHERE universe_version_id = manifest_universe;
    IF universe_pit IS NOT TRUE THEN
        RAISE EXCEPTION 'MARKET_DATA_FINALIZATION_REQUIRES_PIT_UNIVERSE'
            USING ERRCODE = '23514';
    END IF;
    SELECT count(*) INTO actual_members
    FROM universe_members WHERE universe_version_id = manifest_universe;
    IF actual_members <> declared_members THEN
        RAISE EXCEPTION 'MARKET_DATA_FINALIZATION_UNIVERSE_CARDINALITY_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    WITH expected AS (
        SELECT (i->>'instrument_id')::uuid AS instrument_id, slot AS event_time
        FROM jsonb_array_elements(manifest_doc->'windows') AS w
        CROSS JOIN LATERAL jsonb_array_elements(w->'instruments') AS i
        CROSS JOIN LATERAL generate_series(
            (w->>'start')::timestamptz,
            (w->>'end')::timestamptz - make_interval(secs => (w->>'interval_seconds')::integer),
            make_interval(secs => (w->>'interval_seconds')::integer)
        ) AS slot
    ), actual AS (
        SELECT instrument_id, event_time
        FROM market_bars WHERE dataset_version_id = NEW.dataset_version_id
    )
    SELECT
        (SELECT count(*) FROM expected),
        (SELECT count(*) FROM actual),
        (SELECT count(*) FROM (SELECT DISTINCT instrument_id, event_time FROM actual) d),
        (SELECT count(*) FROM (SELECT * FROM expected EXCEPT SELECT * FROM actual) m),
        (SELECT count(*) FROM (SELECT * FROM actual EXCEPT SELECT * FROM expected) e)
    INTO expected_count, actual_count, actual_distinct_count, missing_count, extra_count;

    IF expected_count = 0
       OR actual_count <> actual_distinct_count
       OR missing_count <> 0
       OR extra_count <> 0 THEN
        RAISE EXCEPTION 'MARKET_DATA_FINALIZATION_COVERAGE_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_market_data_version_finalization ON dataset_versions;
CREATE TRIGGER trg_market_data_version_finalization
BEFORE UPDATE ON dataset_versions
FOR EACH ROW EXECUTE FUNCTION hope_guard_market_data_version_finalization();
