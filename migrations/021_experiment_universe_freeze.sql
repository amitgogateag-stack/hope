-- Universe provenance used by an experiment must remain frozen thereafter.
-- Universe construction may continue before first use, but once referenced by an
-- immutable experiment neither version metadata nor membership may change.

CREATE OR REPLACE FUNCTION hope_reject_referenced_universe_version_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM experiments
        WHERE universe_version_id = OLD.universe_version_id
    ) THEN
        RAISE EXCEPTION 'EXPERIMENT_UNIVERSE_IMMUTABLE: referenced universe version cannot be modified or deleted'
            USING ERRCODE = '23514';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_referenced_universe_version_immutable ON universe_versions;
CREATE TRIGGER trg_referenced_universe_version_immutable
BEFORE UPDATE OR DELETE ON universe_versions
FOR EACH ROW EXECUTE FUNCTION hope_reject_referenced_universe_version_mutation();

CREATE OR REPLACE FUNCTION hope_reject_referenced_universe_member_append()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM experiments
        WHERE universe_version_id = NEW.universe_version_id
    ) THEN
        RAISE EXCEPTION 'EXPERIMENT_UNIVERSE_IMMUTABLE: referenced universe membership cannot be extended'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_referenced_universe_member_append ON universe_members;
CREATE TRIGGER trg_referenced_universe_member_append
BEFORE INSERT ON universe_members
FOR EACH ROW EXECUTE FUNCTION hope_reject_referenced_universe_member_append();
