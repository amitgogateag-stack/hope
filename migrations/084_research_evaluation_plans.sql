-- Freeze the full research evaluation method before control or variant execution.
CREATE TABLE research_evaluation_plans (
    variant_experiment_id TEXT PRIMARY KEY
        REFERENCES experiment_variants(variant_experiment_id),
    protocol_hash CHAR(64) NOT NULL CHECK (protocol_hash ~ '^[0-9a-f]{64}$'),
    canonical_protocol JSONB NOT NULL,
    predeclared_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION guard_research_evaluation_plan_insert()
RETURNS trigger AS $$
DECLARE
    control_experiment_id TEXT;
    required_stages TEXT[] := ARRAY[
        'regression_invariants',
        'historical_evaluation',
        'walk_forward',
        'regime_analysis',
        'parameter_sensitivity',
        'cost_stress',
        'slippage_stress',
        'universe_perturbation',
        'contribution_analysis'
    ];
    stage TEXT;
BEGIN
    SELECT ev.control_experiment_id
    INTO control_experiment_id
    FROM experiment_variants ev
    WHERE ev.variant_experiment_id = NEW.variant_experiment_id;

    IF EXISTS (
        SELECT 1 FROM research_runs rr
        WHERE rr.experiment_id IN (control_experiment_id, NEW.variant_experiment_id)
    ) THEN
        RAISE EXCEPTION 'RESEARCH_EVALUATION_PLAN_MUST_PRECEDE_RUNS'
            USING ERRCODE = '23514';
    END IF;

    FOREACH stage IN ARRAY required_stages LOOP
        IF NOT (NEW.canonical_protocol ? stage)
           OR jsonb_typeof(NEW.canonical_protocol->stage) <> 'object'
           OR NEW.canonical_protocol->stage = '{}'::jsonb THEN
            RAISE EXCEPTION USING
                MESSAGE = 'RESEARCH_EVALUATION_PLAN_STAGE_REQUIRED:' || stage,
                ERRCODE = '23514';
        END IF;
    END LOOP;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_research_evaluation_plan_insert_guard
BEFORE INSERT ON research_evaluation_plans
FOR EACH ROW EXECUTE FUNCTION guard_research_evaluation_plan_insert();

CREATE OR REPLACE FUNCTION prevent_research_evaluation_plan_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'RESEARCH_EVALUATION_PLAN_IMMUTABLE' USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_research_evaluation_plan_immutable
BEFORE UPDATE OR DELETE ON research_evaluation_plans
FOR EACH ROW EXECUTE FUNCTION prevent_research_evaluation_plan_mutation();

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
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM research_evaluation_plans rep
        WHERE rep.variant_experiment_id = NEW.variant_experiment_id
    ) THEN
        RAISE EXCEPTION 'RESEARCH_COMPARISON_EVALUATION_PLAN_REQUIRED'
            USING ERRCODE = '23514';
    END IF;

    SELECT ev.control_experiment_id
    INTO expected_control_experiment_id
    FROM experiment_variants ev
    WHERE ev.variant_experiment_id = NEW.variant_experiment_id;

    SELECT rr.experiment_id, rr.as_of
    INTO control_experiment_id, control_as_of
    FROM research_runs rr
    WHERE rr.research_run_id = NEW.control_run_id;

    SELECT rr.experiment_id, rr.as_of
    INTO variant_run_experiment_id, variant_as_of
    FROM research_runs rr
    WHERE rr.research_run_id = NEW.variant_run_id;

    IF control_experiment_id IS DISTINCT FROM expected_control_experiment_id THEN
        RAISE EXCEPTION 'RESEARCH_COMPARISON_CONTROL_RUN_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    IF variant_run_experiment_id IS DISTINCT FROM NEW.variant_experiment_id THEN
        RAISE EXCEPTION 'RESEARCH_COMPARISON_VARIANT_RUN_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    IF control_as_of IS DISTINCT FROM variant_as_of THEN
        RAISE EXCEPTION 'RESEARCH_COMPARISON_AS_OF_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    SELECT rre.result_fingerprint
    INTO stored_control_result_fingerprint
    FROM research_run_evidence rre
    WHERE rre.research_run_id = NEW.control_run_id;

    SELECT rre.result_fingerprint
    INTO stored_variant_result_fingerprint
    FROM research_run_evidence rre
    WHERE rre.research_run_id = NEW.variant_run_id;

    IF stored_control_result_fingerprint IS NULL THEN
        RAISE EXCEPTION 'RESEARCH_COMPARISON_CONTROL_EVIDENCE_REQUIRED'
            USING ERRCODE = '23514';
    END IF;

    IF stored_variant_result_fingerprint IS NULL THEN
        RAISE EXCEPTION 'RESEARCH_COMPARISON_VARIANT_EVIDENCE_REQUIRED'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.control_result_fingerprint IS DISTINCT FROM stored_control_result_fingerprint THEN
        RAISE EXCEPTION 'RESEARCH_COMPARISON_CONTROL_FINGERPRINT_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.variant_result_fingerprint IS DISTINCT FROM stored_variant_result_fingerprint THEN
        RAISE EXCEPTION 'RESEARCH_COMPARISON_VARIANT_FINGERPRINT_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
