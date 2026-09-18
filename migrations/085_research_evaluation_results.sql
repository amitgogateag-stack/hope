CREATE TABLE research_evaluation_results (
    variant_experiment_id TEXT NOT NULL REFERENCES experiment_variants(variant_experiment_id),
    control_run_id UUID NOT NULL REFERENCES research_runs(research_run_id),
    variant_run_id UUID NOT NULL REFERENCES research_runs(research_run_id),
    stage TEXT NOT NULL CHECK (stage IN (
        'regression_invariants','historical_evaluation','walk_forward',
        'regime_analysis','parameter_sensitivity','cost_stress',
        'slippage_stress','universe_perturbation','contribution_analysis'
    )),
    protocol_hash CHAR(64) NOT NULL CHECK (protocol_hash ~ '^[0-9a-f]{64}$'),
    result_fingerprint CHAR(64) NOT NULL CHECK (result_fingerprint ~ '^[0-9a-f]{64}$'),
    canonical_result JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (variant_experiment_id, control_run_id, variant_run_id, stage),
    CHECK (control_run_id <> variant_run_id)
);

CREATE OR REPLACE FUNCTION guard_research_evaluation_result_insert()
RETURNS trigger AS $$
DECLARE
    expected_control TEXT;
    control_experiment TEXT;
    variant_experiment TEXT;
    control_as_of TIMESTAMPTZ;
    variant_as_of TIMESTAMPTZ;
    expected_protocol_hash CHAR(64);
BEGIN
    SELECT ev.control_experiment_id INTO expected_control
    FROM experiment_variants ev
    WHERE ev.variant_experiment_id = NEW.variant_experiment_id;

    SELECT rr.experiment_id, rr.as_of INTO control_experiment, control_as_of
    FROM research_runs rr WHERE rr.research_run_id = NEW.control_run_id;

    SELECT rr.experiment_id, rr.as_of INTO variant_experiment, variant_as_of
    FROM research_runs rr WHERE rr.research_run_id = NEW.variant_run_id;

    SELECT rep.protocol_hash INTO expected_protocol_hash
    FROM research_evaluation_plans rep
    WHERE rep.variant_experiment_id = NEW.variant_experiment_id;

    IF expected_protocol_hash IS NULL THEN
        RAISE EXCEPTION 'RESEARCH_EVALUATION_RESULT_PLAN_REQUIRED' USING ERRCODE='23514';
    END IF;
    IF NEW.protocol_hash IS DISTINCT FROM expected_protocol_hash THEN
        RAISE EXCEPTION 'RESEARCH_EVALUATION_RESULT_PROTOCOL_HASH_MISMATCH' USING ERRCODE='23514';
    END IF;
    IF control_experiment IS DISTINCT FROM expected_control THEN
        RAISE EXCEPTION 'RESEARCH_EVALUATION_RESULT_CONTROL_RUN_MISMATCH' USING ERRCODE='23514';
    END IF;
    IF variant_experiment IS DISTINCT FROM NEW.variant_experiment_id THEN
        RAISE EXCEPTION 'RESEARCH_EVALUATION_RESULT_VARIANT_RUN_MISMATCH' USING ERRCODE='23514';
    END IF;
    IF control_as_of IS DISTINCT FROM variant_as_of THEN
        RAISE EXCEPTION 'RESEARCH_EVALUATION_RESULT_AS_OF_MISMATCH' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM research_run_evidence WHERE research_run_id=NEW.control_run_id) THEN
        RAISE EXCEPTION 'RESEARCH_EVALUATION_RESULT_CONTROL_EVIDENCE_REQUIRED' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM research_run_evidence WHERE research_run_id=NEW.variant_run_id) THEN
        RAISE EXCEPTION 'RESEARCH_EVALUATION_RESULT_VARIANT_EVIDENCE_REQUIRED' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_research_evaluation_result_insert_guard
BEFORE INSERT ON research_evaluation_results
FOR EACH ROW EXECUTE FUNCTION guard_research_evaluation_result_insert();

CREATE OR REPLACE FUNCTION prevent_research_evaluation_result_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'RESEARCH_EVALUATION_RESULT_IMMUTABLE' USING ERRCODE='23514';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_research_evaluation_result_immutable
BEFORE UPDATE OR DELETE ON research_evaluation_results
FOR EACH ROW EXECUTE FUNCTION prevent_research_evaluation_result_mutation();

CREATE OR REPLACE FUNCTION guard_research_comparison_insert()
RETURNS trigger AS $$
DECLARE
    expected_control_experiment_id TEXT;
    control_experiment_id TEXT;
    variant_run_experiment_id TEXT;
    control_as_of TIMESTAMPTZ;
    variant_as_of TIMESTAMPTZ;
    stored_control_result_fingerprint CHAR(64);
    stored_variant_result_fingerprint CHAR(64);
    expected_protocol_hash CHAR(64);
    completed_stage_count INTEGER;
BEGIN
    SELECT rep.protocol_hash INTO expected_protocol_hash
    FROM research_evaluation_plans rep
    WHERE rep.variant_experiment_id = NEW.variant_experiment_id;

    IF expected_protocol_hash IS NULL THEN
        RAISE EXCEPTION 'RESEARCH_COMPARISON_EVALUATION_PLAN_REQUIRED' USING ERRCODE='23514';
    END IF;

    SELECT count(*) INTO completed_stage_count
    FROM research_evaluation_results rer
    WHERE rer.variant_experiment_id=NEW.variant_experiment_id
      AND rer.control_run_id=NEW.control_run_id
      AND rer.variant_run_id=NEW.variant_run_id
      AND rer.protocol_hash=expected_protocol_hash;

    IF completed_stage_count <> 9 THEN
        RAISE EXCEPTION 'RESEARCH_COMPARISON_EVALUATION_RESULTS_INCOMPLETE' USING ERRCODE='23514';
    END IF;

    SELECT ev.control_experiment_id INTO expected_control_experiment_id
    FROM experiment_variants ev WHERE ev.variant_experiment_id=NEW.variant_experiment_id;

    SELECT rr.experiment_id, rr.as_of INTO control_experiment_id, control_as_of
    FROM research_runs rr WHERE rr.research_run_id=NEW.control_run_id;

    SELECT rr.experiment_id, rr.as_of INTO variant_run_experiment_id, variant_as_of
    FROM research_runs rr WHERE rr.research_run_id=NEW.variant_run_id;

    IF control_experiment_id IS DISTINCT FROM expected_control_experiment_id THEN
        RAISE EXCEPTION 'RESEARCH_COMPARISON_CONTROL_RUN_MISMATCH' USING ERRCODE='23514';
    END IF;
    IF variant_run_experiment_id IS DISTINCT FROM NEW.variant_experiment_id THEN
        RAISE EXCEPTION 'RESEARCH_COMPARISON_VARIANT_RUN_MISMATCH' USING ERRCODE='23514';
    END IF;
    IF control_as_of IS DISTINCT FROM variant_as_of THEN
        RAISE EXCEPTION 'RESEARCH_COMPARISON_AS_OF_MISMATCH' USING ERRCODE='23514';
    END IF;

    SELECT result_fingerprint INTO stored_control_result_fingerprint
    FROM research_run_evidence WHERE research_run_id=NEW.control_run_id;
    SELECT result_fingerprint INTO stored_variant_result_fingerprint
    FROM research_run_evidence WHERE research_run_id=NEW.variant_run_id;

    IF stored_control_result_fingerprint IS NULL THEN
        RAISE EXCEPTION 'RESEARCH_COMPARISON_CONTROL_EVIDENCE_REQUIRED' USING ERRCODE='23514';
    END IF;
    IF stored_variant_result_fingerprint IS NULL THEN
        RAISE EXCEPTION 'RESEARCH_COMPARISON_VARIANT_EVIDENCE_REQUIRED' USING ERRCODE='23514';
    END IF;
    IF NEW.control_result_fingerprint IS DISTINCT FROM stored_control_result_fingerprint THEN
        RAISE EXCEPTION 'RESEARCH_COMPARISON_CONTROL_FINGERPRINT_MISMATCH' USING ERRCODE='23514';
    END IF;
    IF NEW.variant_result_fingerprint IS DISTINCT FROM stored_variant_result_fingerprint THEN
        RAISE EXCEPTION 'RESEARCH_COMPARISON_VARIANT_FINGERPRINT_MISMATCH' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
