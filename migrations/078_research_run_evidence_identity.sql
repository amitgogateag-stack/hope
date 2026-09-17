-- Bind certified v2 result evidence to the immutable parent research-run identity.
CREATE OR REPLACE FUNCTION guard_certified_research_run_evidence_identity()
RETURNS trigger AS $$
DECLARE
    parent_experiment_id TEXT;
    parent_run_fingerprint CHAR(64);
    research_provenance JSONB;
BEGIN
    -- Generic research evidence keeps its existing contract. The stricter
    -- lineage check applies only to certified backtest v2 envelopes.
    IF NEW.canonical_result->>'schema' IS DISTINCT FROM 'hope.certified-backtest-result.v2' THEN
        RETURN NEW;
    END IF;

    IF jsonb_typeof(NEW.canonical_result) <> 'object'
       OR NOT (NEW.canonical_result ? 'execution_provenance')
       OR NOT (NEW.canonical_result ? 'research_provenance')
       OR NOT (NEW.canonical_result ? 'backtest') THEN
        RAISE EXCEPTION 'RESEARCH_RUN_CERTIFIED_EVIDENCE_STRUCTURE_INVALID'
            USING ERRCODE = '23514';
    END IF;

    research_provenance := NEW.canonical_result->'research_provenance';
    IF research_provenance IS NULL
       OR jsonb_typeof(research_provenance) <> 'object'
       OR research_provenance->>'experiment_id' IS NULL
       OR research_provenance->>'run_fingerprint' IS NULL THEN
        RAISE EXCEPTION 'RESEARCH_RUN_CERTIFIED_EVIDENCE_PROVENANCE_REQUIRED'
            USING ERRCODE = '23514';
    END IF;

    SELECT rr.experiment_id, rr.run_fingerprint
    INTO parent_experiment_id, parent_run_fingerprint
    FROM research_runs rr
    WHERE rr.research_run_id = NEW.research_run_id;

    IF parent_experiment_id IS NULL THEN
        RAISE EXCEPTION 'RESEARCH_RUN_CERTIFIED_EVIDENCE_PARENT_RUN_MISSING'
            USING ERRCODE = '23503';
    END IF;

    IF research_provenance->>'experiment_id' IS DISTINCT FROM parent_experiment_id THEN
        RAISE EXCEPTION 'RESEARCH_RUN_CERTIFIED_EVIDENCE_EXPERIMENT_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    IF research_provenance->>'run_fingerprint' IS DISTINCT FROM parent_run_fingerprint::text THEN
        RAISE EXCEPTION 'RESEARCH_RUN_CERTIFIED_EVIDENCE_RUN_FINGERPRINT_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_research_run_certified_evidence_identity
BEFORE INSERT ON research_run_evidence
FOR EACH ROW EXECUTE FUNCTION guard_certified_research_run_evidence_identity();
