-- Preserve the canonical provider request-window contract even when a market-data
-- version is finalized directly in SQL. Python requests require aware timestamps,
-- positive whole-second cadence, increasing bounds, and exact interval alignment;
-- the database sealing boundary must enforce the same evidence shape.

CREATE OR REPLACE FUNCTION hope_guard_market_data_manifest_window_contract()
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

    IF EXISTS (
        SELECT 1
        FROM jsonb_array_elements(manifest_doc->'windows') AS w
        WHERE w->>'start' IS NULL
           OR w->>'end' IS NULL
           OR w->>'interval_seconds' IS NULL
           OR w->>'start' !~ '(Z|[+-][0-9]{2}:[0-9]{2})$'
           OR w->>'end' !~ '(Z|[+-][0-9]{2}:[0-9]{2})$'
           OR (w->>'interval_seconds')::integer <= 0
           OR (w->>'end')::timestamptz <= (w->>'start')::timestamptz
           OR mod(
                EXTRACT(EPOCH FROM (
                    (w->>'end')::timestamptz - (w->>'start')::timestamptz
                ))::numeric,
                (w->>'interval_seconds')::numeric
              ) <> 0
    ) THEN
        RAISE EXCEPTION 'MARKET_DATA_FINALIZATION_WINDOW_CONTRACT_INVALID'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_market_data_manifest_window_contract ON dataset_versions;
CREATE TRIGGER trg_market_data_manifest_window_contract
BEFORE UPDATE ON dataset_versions
FOR EACH ROW EXECUTE FUNCTION hope_guard_market_data_manifest_window_contract();
