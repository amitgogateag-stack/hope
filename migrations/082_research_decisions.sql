-- Immutable research decisions must be bound to completed, comparable evidence-backed runs.
CREATE TABLE research_decisions (
    decision_id TEXT PRIMARY KEY CHECK (
        btrim(decision_id) <> ''
        AND decision_id = btrim(decision_id)
    ),
    variant_experiment_id TEXT NOT NULL REFERENCES experiment_variants(variant_experiment_id),
    control_run_id UUID NOT NULL REFERENCES research_runs(research_run_id),
    variant_run_id UUID NOT NULL REFERENCES research_runs(research_run_id),
    decision TEXT NOT NULL CHECK (
        decision IN (
            'SUPPORTED',
            'WEAK_EVIDENCE',
            'INCONCLUSIVE',
            'REJECTED',
            'INVALIDATED',
            'REQUIRES_MORE_DATA'
        )
    ),
    rationale TEXT NOT NULL CHECK (
        btrim(rationale) <> ''
        AND rationale = btrim(rationale)
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (variant_experiment_id, control_run_id, variant_run_id),
    CHECK (control_run_id <> variant_run_id)
);

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
        SELECT 1 FROM research_run_evidence rre
        WHERE rre.research_run_id = NEW.control_run_id
    ) THEN
        RAISE EXCEPTION 'RESEARCH_DECISION_CONTROL_EVIDENCE_REQUIRED'
            USING ERRCODE = '23514';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM research_run_evidence rre
        WHERE rre.research_run_id = NEW.variant_run_id
    ) THEN
        RAISE EXCEPTION 'RESEARCH_DECISION_VARIANT_EVIDENCE_REQUIRED'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_research_decision_insert_guard
BEFORE INSERT ON research_decisions
FOR EACH ROW EXECUTE FUNCTION guard_research_decision_insert();

CREATE OR REPLACE FUNCTION prevent_research_decision_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'RESEARCH_DECISION_IMMUTABLE' USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_research_decision_immutable
BEFORE UPDATE OR DELETE ON research_decisions
FOR EACH ROW EXECUTE FUNCTION prevent_research_decision_mutation();
