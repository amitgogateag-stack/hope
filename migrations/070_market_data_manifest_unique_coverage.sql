-- A manifest is cardinality-bearing evidence. Reject duplicate logical keys at
-- the database finalization boundary so set comparison cannot collapse two
-- declarations into one persisted bar.

CREATE OR REPLACE FUNCTION hope_guard_market_data_manifest_unique_coverage()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    manifest_doc JSONB;
    expected_count BIGINT;
    distinct_expected_count BIGINT;
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

    WITH expected AS (
        SELECT (i->>'instrument_id')::uuid AS instrument_id, slot AS event_time
        FROM jsonb_array_elements(manifest_doc->'windows') AS w
        CROSS JOIN LATERAL jsonb_array_elements(w->'instruments') AS i
        CROSS JOIN LATERAL generate_series(
            (w->>'start')::timestamptz,
            (w->>'end')::timestamptz - make_interval(secs => (w->>'interval_seconds')::integer),
            make_interval(secs => (w->>'interval_seconds')::integer)
        ) AS slot
    )
    SELECT count(*), count(DISTINCT (instrument_id, event_time))
    INTO expected_count, distinct_expected_count
    FROM expected;

    IF expected_count <> distinct_expected_count THEN
        RAISE EXCEPTION 'MARKET_DATA_FINALIZATION_DUPLICATE_MANIFEST_COVERAGE'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_market_data_manifest_unique_coverage ON dataset_versions;
CREATE TRIGGER trg_market_data_manifest_unique_coverage
BEFORE UPDATE ON dataset_versions
FOR EACH ROW EXECUTE FUNCTION hope_guard_market_data_manifest_unique_coverage();
