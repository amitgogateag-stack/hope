-- Once an experiment references a universe version, its exact version metadata
-- is provenance and must remain immutable.
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
    RETURN OLD;
END;
$$;

DROP TRIGGER IF EXISTS trg_referenced_universe_version_immutable ON universe_versions;
CREATE TRIGGER trg_referenced_universe_version_immutable
BEFORE UPDATE OR DELETE ON universe_versions
FOR EACH ROW EXECUTE FUNCTION hope_reject_referenced_universe_version_mutation();
