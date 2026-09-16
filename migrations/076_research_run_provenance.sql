-- Immutable scientific identity for authoritative research executions.
CREATE TABLE research_runs (
    research_run_id UUID PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
    run_fingerprint CHAR(64) NOT NULL CHECK (run_fingerprint ~ '^[0-9a-f]{64}$'),
    as_of TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (experiment_id, run_fingerprint)
);

CREATE OR REPLACE FUNCTION guard_research_run_insert()
RETURNS trigger AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM experiment_invalidations ei
        WHERE ei.experiment_id = NEW.experiment_id
    ) THEN
        RAISE EXCEPTION 'RESEARCH_RUN_EXPERIMENT_INVALIDATED' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_research_run_insert_guard
BEFORE INSERT ON research_runs
FOR EACH ROW EXECUTE FUNCTION guard_research_run_insert();

CREATE OR REPLACE FUNCTION prevent_research_run_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'RESEARCH_RUN_IMMUTABLE' USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_research_run_immutable
BEFORE UPDATE OR DELETE ON research_runs
FOR EACH ROW EXECUTE FUNCTION prevent_research_run_mutation();
