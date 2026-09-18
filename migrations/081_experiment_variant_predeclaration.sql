-- Predeclare fair control/variant comparisons before research evidence exists.
CREATE TABLE experiment_variants (
    variant_experiment_id TEXT PRIMARY KEY REFERENCES experiments(experiment_id),
    control_experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
    variant_label TEXT NOT NULL CHECK (
        btrim(variant_label) <> ''
        AND variant_label = btrim(variant_label)
    ),
    predeclared_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (variant_experiment_id <> control_experiment_id)
);

CREATE OR REPLACE FUNCTION guard_experiment_variant_predeclaration()
RETURNS trigger AS $$
DECLARE
    control_dataset_version_id UUID;
    variant_dataset_version_id UUID;
    control_universe_version_id UUID;
    variant_universe_version_id UUID;
    control_strategy_version_id UUID;
    variant_strategy_version_id UUID;
    control_configuration_hash CHAR(64);
    variant_configuration_hash CHAR(64);
    control_environment TEXT;
    variant_environment TEXT;
BEGIN
    IF NEW.control_experiment_id = NEW.variant_experiment_id THEN
        RAISE EXCEPTION 'EXPERIMENT_VARIANT_CONTROL_MUST_DIFFER'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1 FROM experiment_invalidations ei
        WHERE ei.experiment_id IN (NEW.control_experiment_id, NEW.variant_experiment_id)
    ) THEN
        RAISE EXCEPTION 'EXPERIMENT_VARIANT_REQUIRES_ACTIVE_EXPERIMENTS'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1 FROM research_runs rr
        WHERE rr.experiment_id IN (NEW.control_experiment_id, NEW.variant_experiment_id)
    ) THEN
        RAISE EXCEPTION 'EXPERIMENT_VARIANT_MUST_BE_PREDECLARED_BEFORE_RUNS'
            USING ERRCODE = '23514';
    END IF;

    SELECT dataset_version_id, universe_version_id, strategy_version_id,
           configuration_hash, environment
    INTO control_dataset_version_id, control_universe_version_id,
         control_strategy_version_id, control_configuration_hash,
         control_environment
    FROM experiments
    WHERE experiment_id = NEW.control_experiment_id;

    SELECT dataset_version_id, universe_version_id, strategy_version_id,
           configuration_hash, environment
    INTO variant_dataset_version_id, variant_universe_version_id,
         variant_strategy_version_id, variant_configuration_hash,
         variant_environment
    FROM experiments
    WHERE experiment_id = NEW.variant_experiment_id;

    IF control_dataset_version_id IS DISTINCT FROM variant_dataset_version_id THEN
        RAISE EXCEPTION 'EXPERIMENT_VARIANT_DATASET_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    IF control_universe_version_id IS DISTINCT FROM variant_universe_version_id THEN
        RAISE EXCEPTION 'EXPERIMENT_VARIANT_UNIVERSE_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    IF control_environment IS DISTINCT FROM variant_environment THEN
        RAISE EXCEPTION 'EXPERIMENT_VARIANT_ENVIRONMENT_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    IF control_strategy_version_id IS NOT DISTINCT FROM variant_strategy_version_id
       AND control_configuration_hash IS NOT DISTINCT FROM variant_configuration_hash THEN
        RAISE EXCEPTION 'EXPERIMENT_VARIANT_REQUIRES_MATERIAL_CHANGE'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_experiment_variant_predeclaration_guard
BEFORE INSERT ON experiment_variants
FOR EACH ROW EXECUTE FUNCTION guard_experiment_variant_predeclaration();

CREATE OR REPLACE FUNCTION prevent_experiment_variant_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'EXPERIMENT_VARIANT_IMMUTABLE' USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_experiment_variant_immutable
BEFORE UPDATE OR DELETE ON experiment_variants
FOR EACH ROW EXECUTE FUNCTION prevent_experiment_variant_mutation();
