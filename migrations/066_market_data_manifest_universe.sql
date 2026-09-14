-- Bind durable market-data coverage intent to immutable PIT universe membership.
ALTER TABLE market_data_coverage_manifests
    ADD COLUMN universe_version_id UUID REFERENCES universe_versions(universe_version_id);

ALTER TABLE market_data_coverage_manifests
    ADD CONSTRAINT ck_market_data_manifest_universe_required
    CHECK (universe_version_id IS NOT NULL) NOT VALID;

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
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_market_data_manifest_universe_version_immutable ON universe_versions;
CREATE TRIGGER trg_market_data_manifest_universe_version_immutable
BEFORE UPDATE OR DELETE ON universe_versions
FOR EACH ROW EXECUTE FUNCTION hope_guard_manifest_referenced_universe_version();

CREATE OR REPLACE FUNCTION hope_guard_manifest_referenced_universe_member()
RETURNS trigger AS $$
DECLARE
    target_universe_version_id UUID;
BEGIN
    target_universe_version_id := CASE
        WHEN TG_OP = 'INSERT' THEN NEW.universe_version_id
        ELSE OLD.universe_version_id
    END;

    IF EXISTS (
        SELECT 1
        FROM market_data_coverage_manifests
        WHERE universe_version_id = target_universe_version_id
    ) THEN
        RAISE EXCEPTION 'MARKET_DATA_MANIFEST_UNIVERSE_MEMBERSHIP_IMMUTABLE: referenced universe membership cannot be changed'
            USING ERRCODE = '23514';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_market_data_manifest_universe_member_immutable ON universe_members;
CREATE TRIGGER trg_market_data_manifest_universe_member_immutable
BEFORE INSERT OR UPDATE OR DELETE ON universe_members
FOR EACH ROW EXECUTE FUNCTION hope_guard_manifest_referenced_universe_member();
