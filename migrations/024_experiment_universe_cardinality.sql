-- Experiments may only freeze a universe snapshot whose declared cardinality
-- matches the durable membership actually present at creation time.

CREATE OR REPLACE FUNCTION hope_guard_experiment_universe_cardinality()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    declared_count INTEGER;
    actual_count BIGINT;
BEGIN
    SELECT declared_member_count
    INTO declared_count
    FROM universe_versions
    WHERE universe_version_id = NEW.universe_version_id;

    IF declared_count IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT count(*)
    INTO actual_count
    FROM universe_members
    WHERE universe_version_id = NEW.universe_version_id;

    IF actual_count <> declared_count THEN
        RAISE EXCEPTION 'EXPERIMENT_UNIVERSE_MEMBER_COUNT_MISMATCH: declared %, actual %', declared_count, actual_count
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_experiment_universe_cardinality ON experiments;
CREATE TRIGGER trg_experiment_universe_cardinality
BEFORE INSERT ON experiments
FOR EACH ROW EXECUTE FUNCTION hope_guard_experiment_universe_cardinality();
