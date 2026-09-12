-- Experiments must bind only to sealed dataset versions.
-- A mutable staging dataset would allow the same immutable experiment definition
-- to observe changing inputs, violating HOPE's reproducibility contract.

CREATE OR REPLACE FUNCTION hope_guard_experiment_dataset_version_frozen()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    version_immutable BOOLEAN;
BEGIN
    SELECT immutable
    INTO version_immutable
    FROM dataset_versions
    WHERE dataset_version_id = NEW.dataset_version_id;

    -- Preserve the existing foreign-key error for an unknown dataset version.
    IF NOT FOUND THEN
        RETURN NEW;
    END IF;

    IF version_immutable IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'EXPERIMENT_DATASET_VERSION_NOT_FROZEN: dataset version %% must be sealed before experiment creation', NEW.dataset_version_id
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_experiments_dataset_version_frozen ON experiments;
CREATE TRIGGER trg_experiments_dataset_version_frozen
BEFORE INSERT ON experiments
FOR EACH ROW EXECUTE FUNCTION hope_guard_experiment_dataset_version_frozen();
