-- Certified PAPER authorization is opt-in; legacy low-level PAPER APIs remain
-- unchanged. New certified work must call the eligibility proof before claiming
-- and persist this receipt before economic effects, in the same transaction.
CREATE FUNCTION hope_assert_current_paper_eligibility(
    requested_strategy UUID, requested_market TEXT,
    expected_classification UUID, expected_decision TEXT
) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    control_state TEXT;
    candidate RECORD;
    decision RECORD;
    history_max BIGINT;
    generator_last BIGINT;
    generator_called BOOLEAN;
BEGIN
    PERFORM hope_require_fresh_paper_control_snapshot();
    PERFORM pg_advisory_xact_lock(hashtext('hope:paper:environment-control')::bigint);
    SELECT state INTO control_state FROM paper_environment_control_events
        ORDER BY control_sequence DESC LIMIT 1;
    IF control_state IS DISTINCT FROM 'RUNNING' THEN
        RAISE EXCEPTION 'PAPER_STRATEGY_AUTHORIZATION_REQUIRES_RUNNING'
            USING ERRCODE='23514';
    END IF;
    SELECT max(control_sequence) INTO history_max FROM paper_environment_control_events;
    SELECT last_value,is_called INTO generator_last,generator_called
        FROM paper_environment_control_events_control_sequence_seq;
    IF history_max IS NULL OR generator_last < history_max
       OR (generator_last = history_max AND generator_called IS NOT TRUE) THEN
        RAISE EXCEPTION 'PAPER_STRATEGY_AUTHORIZATION_CONTROL_NOT_READY'
            USING ERRCODE='23514';
    END IF;
    -- The same lock serializes all promotion/demotion and evidence invalidation.
    -- Acquiring it under READ COMMITTED precedes the authoritative fresh reads.
    PERFORM pg_advisory_xact_lock(hashtext('hope:strategy-candidate-capacity')::bigint);
    SELECT * INTO candidate FROM current_strategy_candidate_classifications
        WHERE strategy_version_id=requested_strategy;
    IF candidate IS NULL OR candidate.state IS DISTINCT FROM 'OPERATIONAL_CANDIDATE'
       OR candidate.classification_id IS DISTINCT FROM expected_classification
       OR candidate.research_decision_id IS DISTINCT FROM expected_decision
       OR requested_market IS NULL OR requested_market NOT IN ('USA','INDIA')
       OR NOT (requested_market = ANY(candidate.markets)) THEN
        RAISE EXCEPTION 'PAPER_STRATEGY_AUTHORIZATION_INELIGIBLE'
            USING ERRCODE='23514';
    END IF;
    SELECT rd.decision, e.strategy_version_id, e.experiment_id AS variant_experiment,
           ce.experiment_id AS control_experiment
        INTO decision
        FROM research_decisions rd
        JOIN research_runs vr ON vr.research_run_id=rd.variant_run_id
        JOIN experiments e ON e.experiment_id=vr.experiment_id
        JOIN research_runs cr ON cr.research_run_id=rd.control_run_id
        JOIN experiments ce ON ce.experiment_id=cr.experiment_id
        WHERE rd.decision_id=expected_decision;
    IF decision IS NULL OR decision.decision IS DISTINCT FROM 'SUPPORTED'
       OR decision.strategy_version_id IS DISTINCT FROM requested_strategy
       OR EXISTS (SELECT 1 FROM experiment_invalidations
                  WHERE experiment_id IN (decision.variant_experiment,decision.control_experiment)) THEN
        RAISE EXCEPTION 'PAPER_STRATEGY_AUTHORIZATION_RESEARCH_INVALID'
            USING ERRCODE='23514';
    END IF;
END;
$$;

CREATE FUNCTION hope_lock_paper_evidence_invalidation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('hope:strategy-candidate-capacity')::bigint);
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_paper_evidence_invalidation_serialization
BEFORE INSERT ON experiment_invalidations
FOR EACH ROW EXECUTE FUNCTION hope_lock_paper_evidence_invalidation();

CREATE TABLE paper_strategy_authorizations (
    job_run_id UUID PRIMARY KEY REFERENCES job_runs(job_run_id),
    strategy_version_id UUID NOT NULL REFERENCES strategy_versions(strategy_version_id),
    market TEXT NOT NULL CHECK (market IN ('USA','INDIA')),
    classification_id UUID NOT NULL REFERENCES strategy_candidate_classifications(classification_id),
    research_decision_id TEXT NOT NULL REFERENCES research_decisions(decision_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
    database_principal TEXT NOT NULL DEFAULT current_user
);

CREATE FUNCTION hope_guard_paper_strategy_authorization()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    owner RECORD;
BEGIN
    PERFORM hope_assert_current_paper_eligibility(NEW.strategy_version_id, NEW.market,
        NEW.classification_id, NEW.research_decision_id);
    SELECT job_key,status INTO owner FROM job_runs WHERE job_run_id=NEW.job_run_id FOR UPDATE;
    IF owner IS NULL OR owner.status IS DISTINCT FROM 'CLAIMED'
       OR split_part(owner.job_key,':',1) <> 'paper'
       OR split_part(owner.job_key,':',2) IS DISTINCT FROM NEW.market
       OR split_part(owner.job_key,':',3) IS DISTINCT FROM NEW.strategy_version_id::TEXT THEN
        RAISE EXCEPTION 'PAPER_STRATEGY_AUTHORIZATION_JOB_MISMATCH'
            USING ERRCODE='23514';
    END IF;
    IF NEW.created_at IS DISTINCT FROM transaction_timestamp()
       OR NEW.database_principal IS DISTINCT FROM current_user THEN
        RAISE EXCEPTION 'PAPER_STRATEGY_AUTHORIZATION_PROVENANCE_MISMATCH'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_paper_strategy_authorization_insert
BEFORE INSERT ON paper_strategy_authorizations
FOR EACH ROW EXECUTE FUNCTION hope_guard_paper_strategy_authorization();

CREATE FUNCTION hope_reject_paper_strategy_authorization_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'PAPER_STRATEGY_AUTHORIZATION_IMMUTABLE' USING ERRCODE='23514';
END;
$$;
CREATE TRIGGER trg_paper_strategy_authorization_immutable
BEFORE UPDATE OR DELETE ON paper_strategy_authorizations
FOR EACH ROW EXECUTE FUNCTION hope_reject_paper_strategy_authorization_mutation();
