-- Freeze the exact control-vs-variant comparison artifact before a decision is recorded.
CREATE TABLE research_comparisons (
    comparison_id UUID PRIMARY KEY,
    variant_experiment_id TEXT NOT NULL REFERENCES experiment_variants(variant_experiment_id),
    control_run_id UUID NOT NULL REFERENCES research_runs(research_run_id),
    variant_run_id UUID NOT NULL REFERENCES research_runs(research_run_id),
    control_result_fingerprint CHAR(64) NOT NULL CHECK (
        control_result_fingerprint ~ '^[0-9a-f]{64}$'
    ),
    variant_result_fingerprint CHAR(64) NOT NULL CHECK (
        variant_result_fingerprint ~ '^[0-9a-f]{64}$'
    ),
    comparison_fingerprint CHAR(64) NOT NULL CHECK (
        comparison_fingerprint ~ '^[0-9a-f]{64}$'
    ),
    canonical_comparison JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (variant_experiment_id, control_run_id, variant_run_id),
    CHECK (control_run_id <> variant_run_id)
);

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

CREATE TRIGGER trg_research_comparison_insert_guard
BEFORE INSERT ON research_comparisons
FOR EACH ROW EXECUTE FUNCTION guard_research_comparison_insert();

CREATE OR REPLACE FUNCTION prevent_research_comparison_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'RESEARCH_COMPARISON_IMMUTABLE' USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_research_comparison_immutable
BEFORE UPDATE OR DELETE ON research_comparisons
FOR EACH ROW EXECUTE FUNCTION prevent_research_comparison_mutation();

CREATE OR REPLACE FUNCTION guard_research_decision_insert()
RETURNS trigger AS $$
DECLARE
    expected_control_experiment_id TEXT;
    control_experiment_id TEXT;
    variant_run_experiment_id TEXT;
    control_as_of TIMESTAMPTZ;
    variant_as_of TIMESTAMPTZ;
BEGIN
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
        RAISE EXCEPTION 'RESEARCH_DECISION_CONTROL_RUN_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    IF variant_run_experiment_id IS DISTINCT FROM NEW.variant_experiment_id THEN
        RAISE EXCEPTION 'RESEARCH_DECISION_VARIANT_RUN_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    IF control_as_of IS DISTINCT FROM variant_as_of THEN
        RAISE EXCEPTION 'RESEARCH_DECISION_AS_OF_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM research_comparisons rc
        WHERE rc.variant_experiment_id = NEW.variant_experiment_id
          AND rc.control_run_id = NEW.control_run_id
          AND rc.variant_run_id = NEW.variant_run_id
    ) THEN
        RAISE EXCEPTION 'RESEARCH_DECISION_COMPARISON_REQUIRED'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
