-- Append-only strategy candidate registry for India/US research operations.
CREATE TABLE strategy_candidate_classifications (
    classification_id UUID PRIMARY KEY,
    strategy_version_id UUID NOT NULL
        REFERENCES strategy_versions(strategy_version_id),
    markets TEXT[] NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('RESEARCH','BACKUP_CANDIDATE','OPERATIONAL_CANDIDATE')
    ),
    research_decision_id TEXT REFERENCES research_decisions(decision_id),
    rationale TEXT NOT NULL CHECK (
        btrim(rationale) <> '' AND rationale = btrim(rationale)
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (
        markets = ARRAY['INDIA']::TEXT[]
        OR markets = ARRAY['USA']::TEXT[]
        OR markets = ARRAY['INDIA','USA']::TEXT[]
    ),
    CHECK (
        state = 'RESEARCH'
        OR research_decision_id IS NOT NULL
    )
);

CREATE OR REPLACE FUNCTION guard_strategy_candidate_classification_insert()
RETURNS trigger AS $$
DECLARE
    decision_strategy_version_id UUID;
BEGIN
    IF NEW.state <> 'RESEARCH' THEN
        SELECT e.strategy_version_id
        INTO decision_strategy_version_id
        FROM research_decisions rd
        JOIN research_runs rr
          ON rr.research_run_id = rd.variant_run_id
        JOIN experiments e
          ON e.experiment_id = rr.experiment_id
        WHERE rd.decision_id = NEW.research_decision_id;

        IF decision_strategy_version_id IS DISTINCT FROM NEW.strategy_version_id THEN
            RAISE EXCEPTION 'STRATEGY_CANDIDATE_DECISION_STRATEGY_MISMATCH'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_strategy_candidate_classification_insert_guard
BEFORE INSERT ON strategy_candidate_classifications
FOR EACH ROW EXECUTE FUNCTION guard_strategy_candidate_classification_insert();

CREATE OR REPLACE FUNCTION prevent_strategy_candidate_classification_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'STRATEGY_CANDIDATE_CLASSIFICATION_IMMUTABLE'
        USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_strategy_candidate_classification_immutable
BEFORE UPDATE OR DELETE ON strategy_candidate_classifications
FOR EACH ROW EXECUTE FUNCTION prevent_strategy_candidate_classification_mutation();
