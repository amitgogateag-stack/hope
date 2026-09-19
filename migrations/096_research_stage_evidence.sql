-- Immutable stage-specific evidence for deterministic research evaluation.
-- Regression invariants retain their stricter dedicated evidence table.
CREATE TABLE research_stage_evidence (
    research_run_id UUID NOT NULL REFERENCES research_runs(research_run_id),
    stage TEXT NOT NULL CHECK (
        stage IN (
            'walk_forward',
            'regime_analysis',
            'parameter_sensitivity',
            'cost_stress',
            'slippage_stress',
            'universe_perturbation',
            'contribution_analysis'
        )
    ),
    result_fingerprint CHAR(64) NOT NULL CHECK (
        result_fingerprint ~ '^[0-9a-f]{64}$'
    ),
    canonical_result JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (research_run_id, stage)
);

CREATE OR REPLACE FUNCTION prevent_research_stage_evidence_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'RESEARCH_STAGE_EVIDENCE_IMMUTABLE' USING ERRCODE='23514';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_research_stage_evidence_immutable
BEFORE UPDATE OR DELETE ON research_stage_evidence
FOR EACH ROW EXECUTE FUNCTION prevent_research_stage_evidence_mutation();
