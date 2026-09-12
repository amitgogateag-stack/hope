-- Experiment invalidations are historical facts and must remain append-only.

CREATE OR REPLACE FUNCTION hope_reject_experiment_invalidation_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'EXPERIMENT_INVALIDATION_IMMUTABLE: experiment invalidations cannot be modified or deleted'
        USING ERRCODE = '23514';
END;
$$;

DROP TRIGGER IF EXISTS trg_experiment_invalidations_immutable ON experiment_invalidations;
CREATE TRIGGER trg_experiment_invalidations_immutable
BEFORE UPDATE OR DELETE ON experiment_invalidations
FOR EACH ROW EXECUTE FUNCTION hope_reject_experiment_invalidation_mutation();
