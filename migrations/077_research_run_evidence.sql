-- Immutable output evidence for one authoritative research run.
CREATE TABLE research_run_evidence (
    research_run_id UUID PRIMARY KEY REFERENCES research_runs(research_run_id),
    result_fingerprint CHAR(64) NOT NULL CHECK (result_fingerprint ~ '^[0-9a-f]{64}$'),
    canonical_result JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION prevent_research_run_evidence_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'RESEARCH_RUN_EVIDENCE_IMMUTABLE' USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_research_run_evidence_immutable
BEFORE UPDATE OR DELETE ON research_run_evidence
FOR EACH ROW EXECUTE FUNCTION prevent_research_run_evidence_mutation();
