-- Universe metadata becomes provenance once a version exists.
-- Draft universes may be corrected before versioning, but historical universe
-- identity must not be renamed or deleted after a version has been created.

CREATE OR REPLACE FUNCTION hope_guard_versioned_universe_metadata()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM universe_versions
        WHERE universe_id = OLD.universe_id
    ) THEN
        RAISE EXCEPTION 'UNIVERSE_METADATA_IMMUTABLE: versioned universe metadata cannot be modified or deleted'
            USING ERRCODE = '23514';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_versioned_universe_metadata_immutable ON universes;
CREATE TRIGGER trg_versioned_universe_metadata_immutable
BEFORE UPDATE OR DELETE ON universes
FOR EACH ROW EXECUTE FUNCTION hope_guard_versioned_universe_metadata();
