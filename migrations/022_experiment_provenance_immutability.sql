-- Experiment identity and provenance are historical facts.
-- Lifecycle status may evolve, but the inputs that define an experiment may not.

CREATE OR REPLACE FUNCTION hope_guard_experiment_provenance()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'EXPERIMENT_PROVENANCE_IMMUTABLE: experiments cannot be deleted'
            USING ERRCODE = '23514';
    END IF;

    IF ROW(
        NEW.experiment_id,
        NEW.hypothesis,
        NEW.strategy_version_id,
        NEW.dataset_version_id,
        NEW.universe_version_id,
        NEW.configuration_hash,
        NEW.environment,
        NEW.created_at
    ) IS DISTINCT FROM ROW(
        OLD.experiment_id,
        OLD.hypothesis,
        OLD.strategy_version_id,
        OLD.dataset_version_id,
        OLD.universe_version_id,
        OLD.configuration_hash,
        OLD.environment,
        OLD.created_at
    ) THEN
        RAISE EXCEPTION 'EXPERIMENT_PROVENANCE_IMMUTABLE: experiment identity and provenance cannot be modified'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_experiment_provenance_immutable ON experiments;
CREATE TRIGGER trg_experiment_provenance_immutable
BEFORE UPDATE OR DELETE ON experiments
FOR EACH ROW EXECUTE FUNCTION hope_guard_experiment_provenance();
