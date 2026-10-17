-- Observational proof of the certified bridge; accounting remains in the
-- existing PAPER portfolio ledger. No new trading or risk policy is introduced.
CREATE TABLE paper_certified_outcomes (
    job_run_id UUID PRIMARY KEY REFERENCES paper_strategy_authorizations(job_run_id),
    source_run_id UUID NOT NULL REFERENCES research_runs(research_run_id),
    dataset_version_id UUID NOT NULL REFERENCES dataset_versions(dataset_version_id),
    universe_version_id UUID NOT NULL REFERENCES universe_versions(universe_version_id),
    configuration_hash CHAR(64) NOT NULL REFERENCES configuration_snapshots(configuration_hash),
    inputs_hash CHAR(64) NOT NULL CHECK (inputs_hash ~ '^[0-9a-f]{64}$'),
    decision_time TIMESTAMPTZ NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('NO_TRADE','RISK_BLOCKED','FILLED')),
    reason_code TEXT NOT NULL CHECK (length(reason_code) BETWEEN 1 AND 128),
    created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp()
);

CREATE FUNCTION hope_guard_paper_certified_outcome()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    owner RECORD;
    effects TEXT[];
BEGIN
    SELECT j.status,j.scheduled_for,rd.variant_run_id,e.configuration_hash,
           e.universe_version_id,e.dataset_version_id AS research_dataset,
           e.strategy_version_id,a.strategy_version_id AS authorized_version
      INTO owner FROM paper_strategy_authorizations a
      JOIN job_runs j USING(job_run_id)
      JOIN research_decisions rd ON rd.decision_id=a.research_decision_id
      JOIN research_runs r ON r.research_run_id=rd.variant_run_id
      JOIN experiments e ON e.experiment_id=r.experiment_id
      WHERE a.job_run_id=NEW.job_run_id FOR UPDATE OF j;
    IF owner IS NULL OR owner.status IS DISTINCT FROM 'CLAIMED'
       OR NEW.source_run_id IS DISTINCT FROM owner.variant_run_id
       OR NEW.configuration_hash IS DISTINCT FROM owner.configuration_hash
       OR NEW.universe_version_id IS DISTINCT FROM owner.universe_version_id
       OR owner.strategy_version_id IS DISTINCT FROM owner.authorized_version
       OR NEW.decision_time > owner.scheduled_for
       OR NEW.created_at IS DISTINCT FROM transaction_timestamp() THEN
        RAISE EXCEPTION 'PAPER_CERTIFIED_OUTCOME_PROVENANCE_MISMATCH' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM dataset_versions v JOIN datasets d USING(dataset_id)
        JOIN market_data_coverage_manifests m ON m.dataset_version_id=v.dataset_version_id
        JOIN dataset_versions rv ON rv.dataset_version_id=owner.research_dataset
        JOIN datasets rd ON rd.dataset_id=rv.dataset_id
        WHERE v.dataset_version_id=NEW.dataset_version_id AND v.immutable
          AND d.pit_certified AND d.source=rd.source
          AND m.universe_version_id=NEW.universe_version_id
    ) THEN
        RAISE EXCEPTION 'PAPER_CERTIFIED_OUTCOME_DATASET_MISMATCH' USING ERRCODE='23514';
    END IF;
    SELECT array_agg(effect_type ORDER BY effect_type) INTO effects
      FROM paper_effects WHERE job_run_id=NEW.job_run_id;
    IF (NEW.outcome='FILLED' AND effects IS DISTINCT FROM ARRAY['FILL','ORDER','PNL','RISK','SIGNAL']::TEXT[])
       OR (NEW.outcome IN ('NO_TRADE','RISK_BLOCKED') AND effects IS DISTINCT FROM ARRAY['NO_TRADE']::TEXT[])
       OR (NEW.outcome='NO_TRADE' AND NEW.reason_code <> 'NO_SIGNAL')
       OR (NEW.outcome='FILLED' AND NEW.reason_code <> 'PORTFOLIO_RISK_APPROVED') THEN
        RAISE EXCEPTION 'PAPER_CERTIFIED_OUTCOME_EFFECT_MISMATCH' USING ERRCODE='23514';
    END IF;
    IF (NEW.outcome IN ('NO_TRADE','RISK_BLOCKED') AND NOT EXISTS (
            SELECT 1 FROM paper_no_trade_decisions n WHERE n.job_run_id=NEW.job_run_id
              AND n.inputs_hash=NEW.inputs_hash))
       OR (NEW.outcome='FILLED' AND NOT EXISTS (
            SELECT 1 FROM paper_effects p JOIN signals s ON s.signal_id=p.entity_id
            WHERE p.job_run_id=NEW.job_run_id AND p.effect_type='SIGNAL'
              AND s.inputs_hash=NEW.inputs_hash AND s.decision_time=NEW.decision_time)) THEN
        RAISE EXCEPTION 'PAPER_CERTIFIED_OUTCOME_INPUTS_MISMATCH' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_paper_certified_outcome_insert BEFORE INSERT ON paper_certified_outcomes
FOR EACH ROW EXECUTE FUNCTION hope_guard_paper_certified_outcome();
CREATE TRIGGER trg_paper_certified_outcome_immutable BEFORE UPDATE OR DELETE ON paper_certified_outcomes
FOR EACH ROW EXECUTE FUNCTION hope_reject_paper_strategy_authorization_mutation();
