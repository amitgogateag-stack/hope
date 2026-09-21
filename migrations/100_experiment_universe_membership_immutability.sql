-- Once an experiment references a universe version, its exact membership and
-- temporal intervals are provenance and must remain immutable.
CREATE OR REPLACE FUNCTION hope_reject_referenced_universe_member_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    old_universe_version_id UUID;
    new_universe_version_id UUID;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        old_universe_version_id := OLD.universe_version_id;
    END IF;
    IF TG_OP <> 'DELETE' THEN
        new_universe_version_id := NEW.universe_version_id;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM experiments
        WHERE universe_version_id = old_universe_version_id
           OR universe_version_id = new_universe_version_id
    ) THEN
        RAISE EXCEPTION 'EXPERIMENT_UNIVERSE_IMMUTABLE: referenced universe membership cannot be changed'
            USING ERRCODE = '23514';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_referenced_universe_member_append ON universe_members;
DROP TRIGGER IF EXISTS trg_referenced_universe_member_immutable ON universe_members;
CREATE TRIGGER trg_referenced_universe_member_immutable
BEFORE INSERT OR UPDATE OR DELETE ON universe_members
FOR EACH ROW EXECUTE FUNCTION hope_reject_referenced_universe_member_mutation();
