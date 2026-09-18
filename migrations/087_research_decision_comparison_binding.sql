-- Bind every new research decision to the exact immutable comparison it judged.
-- Existing historical rows remain untouched; new inserts fail closed unless explicitly bound.
ALTER TABLE research_decisions
    ADD COLUMN comparison_id UUID,
    ADD COLUMN comparison_fingerprint CHAR(64);

ALTER TABLE research_decisions
    ADD CONSTRAINT fk_research_decisions_comparison
        FOREIGN KEY (comparison_id) REFERENCES research_comparisons(comparison_id),
    ADD CONSTRAINT ck_research_decisions_comparison_fingerprint
        CHECK (
            comparison_fingerprint IS NULL
            OR comparison_fingerprint ~ '^[0-9a-f]{64}$'
        );

CREATE OR REPLACE FUNCTION guard_research_decision_insert()
RETURNS trigger AS $$
DECLARE
    expected_control_experiment_id TEXT;
    control_experiment_id TEXT;
    variant_run_experiment_id TEXT;
    control_as_of TIMESTAMPTZ;
    variant_as_of TIMESTAMPTZ;
    stored_comparison_id UUID;
    stored_comparison_fingerprint CHAR(64);
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

    IF NEW.comparison_id IS NULL OR NEW.comparison_fingerprint IS NULL THEN
        RAISE EXCEPTION 'RESEARCH_DECISION_COMPARISON_BINDING_REQUIRED'
            USING ERRCODE = '23514';
    END IF;

    SELECT rc.comparison_id, rc.comparison_fingerprint
    INTO stored_comparison_id, stored_comparison_fingerprint
    FROM research_comparisons rc
    WHERE rc.variant_experiment_id = NEW.variant_experiment_id
      AND rc.control_run_id = NEW.control_run_id
      AND rc.variant_run_id = NEW.variant_run_id;

    IF stored_comparison_id IS NULL THEN
        RAISE EXCEPTION 'RESEARCH_DECISION_COMPARISON_REQUIRED'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.comparison_id IS DISTINCT FROM stored_comparison_id THEN
        RAISE EXCEPTION 'RESEARCH_DECISION_COMPARISON_ID_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.comparison_fingerprint IS DISTINCT FROM stored_comparison_fingerprint THEN
        RAISE EXCEPTION 'RESEARCH_DECISION_COMPARISON_FINGERPRINT_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
