-- PAPER eligibility is stronger than research-decision validity.  Generic
-- evidence remains useful for research, but it must not authorize a backup or
-- operational candidate.  Promotion requires the current certified backtest
-- envelope for both sides of the comparison judged by the decision.

CREATE OR REPLACE FUNCTION guard_strategy_candidate_classification_insert()
RETURNS trigger AS $$
DECLARE
    decision_strategy_version_id UUID;
    decision_outcome TEXT;
    control_evidence_schema TEXT;
    variant_evidence_schema TEXT;
BEGIN
    IF NEW.state <> 'RESEARCH' THEN
        SELECT
            e.strategy_version_id,
            rd.decision,
            control_evidence.canonical_result ->> 'schema',
            variant_evidence.canonical_result ->> 'schema'
        INTO
            decision_strategy_version_id,
            decision_outcome,
            control_evidence_schema,
            variant_evidence_schema
        FROM research_decisions rd
        JOIN research_runs rr
          ON rr.research_run_id = rd.variant_run_id
        JOIN experiments e
          ON e.experiment_id = rr.experiment_id
        LEFT JOIN research_run_evidence control_evidence
          ON control_evidence.research_run_id = rd.control_run_id
        LEFT JOIN research_run_evidence variant_evidence
          ON variant_evidence.research_run_id = rd.variant_run_id
        WHERE rd.decision_id = NEW.research_decision_id;

        IF decision_strategy_version_id IS DISTINCT FROM NEW.strategy_version_id THEN
            RAISE EXCEPTION 'STRATEGY_CANDIDATE_DECISION_STRATEGY_MISMATCH'
                USING ERRCODE = '23514';
        END IF;

        IF decision_outcome IS DISTINCT FROM 'SUPPORTED' THEN
            RAISE EXCEPTION 'STRATEGY_CANDIDATE_SUPPORTED_DECISION_REQUIRED'
                USING ERRCODE = '23514';
        END IF;

        IF control_evidence_schema IS DISTINCT FROM 'hope.certified-backtest-result.v3' THEN
            RAISE EXCEPTION 'STRATEGY_CANDIDATE_CERTIFIED_CONTROL_EVIDENCE_REQUIRED'
                USING ERRCODE = '23514';
        END IF;

        IF variant_evidence_schema IS DISTINCT FROM 'hope.certified-backtest-result.v3' THEN
            RAISE EXCEPTION 'STRATEGY_CANDIDATE_CERTIFIED_VARIANT_EVIDENCE_REQUIRED'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Refuse to declare the migration complete while a legacy current candidate
-- could still be selected by the autonomous PAPER service without certified
-- control and variant evidence.  Historical, superseded classifications do
-- not affect current authorization and intentionally remain append-only.
DO $$
DECLARE
    unsafe_classification_id UUID;
BEGIN
    SELECT current.classification_id
    INTO unsafe_classification_id
    FROM current_strategy_candidate_classifications current
    JOIN research_decisions rd
      ON rd.decision_id = current.research_decision_id
    LEFT JOIN research_run_evidence control_evidence
      ON control_evidence.research_run_id = rd.control_run_id
    LEFT JOIN research_run_evidence variant_evidence
      ON variant_evidence.research_run_id = rd.variant_run_id
    WHERE current.state <> 'RESEARCH'
      AND (
          control_evidence.canonical_result ->> 'schema'
              IS DISTINCT FROM 'hope.certified-backtest-result.v3'
          OR variant_evidence.canonical_result ->> 'schema'
              IS DISTINCT FROM 'hope.certified-backtest-result.v3'
      )
    ORDER BY current.classification_sequence
    LIMIT 1;

    IF unsafe_classification_id IS NOT NULL THEN
        RAISE EXCEPTION 'STRATEGY_CANDIDATE_CERTIFIED_EVIDENCE_MIGRATION_REQUIRED:%',
            unsafe_classification_id
            USING ERRCODE = '23514';
    END IF;
END;
$$;
