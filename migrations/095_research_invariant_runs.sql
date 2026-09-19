-- Immutable invariant-suite evidence bound to one authoritative research run.
CREATE TABLE research_invariant_runs (
    research_run_id UUID PRIMARY KEY REFERENCES research_runs(research_run_id),
    context_fingerprint CHAR(64) NOT NULL CHECK (
        context_fingerprint ~ '^[0-9a-f]{64}$'
    ),
    result_fingerprint CHAR(64) NOT NULL CHECK (
        result_fingerprint ~ '^[0-9a-f]{64}$'
    ),
    canonical_results JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION prevent_research_invariant_run_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'RESEARCH_INVARIANT_RUN_IMMUTABLE' USING ERRCODE='23514';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_research_invariant_run_immutable
BEFORE UPDATE OR DELETE ON research_invariant_runs
FOR EACH ROW EXECUTE FUNCTION prevent_research_invariant_run_mutation();
