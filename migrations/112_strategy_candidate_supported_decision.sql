-- Candidate promotion must be backed by an affirmative research decision.
-- A non-research classification changes operational eligibility, so merely
-- referencing any research decision is insufficient.

CREATE OR REPLACE FUNCTION guard_strategy_candidate_classification_insert()
RETURNS trigger AS $$
DECLARE
    decision_strategy_version_id UUID;
    decision_outcome TEXT;
BEGIN
    IF NEW.state <> 'RESEARCH' THEN
        SELECT e.strategy_version_id, rd.decision
        INTO decision_strategy_version_id, decision_outcome
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

        IF decision_outcome IS DISTINCT FROM 'SUPPORTED' THEN
            RAISE EXCEPTION 'STRATEGY_CANDIDATE_SUPPORTED_DECISION_REQUIRED'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
