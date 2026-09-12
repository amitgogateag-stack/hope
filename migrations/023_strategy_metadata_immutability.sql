-- Strategy metadata becomes durable provenance once a version exists.
-- Before versioning, draft strategy metadata may be corrected. After the first
-- strategy version is created, changing the parent name/family would rewrite the
-- meaning of immutable strategy-version and experiment provenance.

CREATE OR REPLACE FUNCTION hope_guard_versioned_strategy_metadata()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM strategy_versions
        WHERE strategy_id = OLD.strategy_id
    ) THEN
        RAISE EXCEPTION 'STRATEGY_METADATA_IMMUTABLE: versioned strategy metadata cannot be modified or deleted'
            USING ERRCODE = '23514';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_versioned_strategy_metadata_immutable ON strategies;
CREATE TRIGGER trg_versioned_strategy_metadata_immutable
BEFORE UPDATE OR DELETE ON strategies
FOR EACH ROW EXECUTE FUNCTION hope_guard_versioned_strategy_metadata();
