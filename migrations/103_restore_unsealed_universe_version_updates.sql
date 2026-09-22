-- The market-data manifest provenance guard must freeze only referenced
-- universe versions. Returning OLD for every UPDATE silently suppressed edits
-- to unsealed drafts that have no manifest reference.
CREATE OR REPLACE FUNCTION hope_guard_manifest_referenced_universe_version()
RETURNS trigger AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM market_data_coverage_manifests
        WHERE universe_version_id = OLD.universe_version_id
    ) THEN
        RAISE EXCEPTION 'MARKET_DATA_MANIFEST_UNIVERSE_IMMUTABLE: referenced universe version cannot be modified or deleted'
            USING ERRCODE = '23514';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
