-- Bind each authoritative research run to the exact immutable market-data manifest
-- used by its experiment when that experiment references manifest-backed data.
-- Generic/non-market research remains valid and produces no row in this table.

CREATE TABLE research_run_market_data_provenance (
    research_run_id UUID PRIMARY KEY REFERENCES research_runs(research_run_id),
    dataset_version_id UUID NOT NULL REFERENCES dataset_versions(dataset_version_id),
    universe_version_id UUID NOT NULL REFERENCES universe_versions(universe_version_id),
    manifest_hash CHAR(64) NOT NULL CHECK (manifest_hash ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION bind_research_run_market_data_provenance()
RETURNS trigger AS $$
DECLARE
    experiment_dataset UUID;
    experiment_universe UUID;
    manifest_universe UUID;
    sealed_manifest_hash CHAR(64);
BEGIN
    SELECT e.dataset_version_id, e.universe_version_id,
           m.universe_version_id, m.manifest_hash
    INTO experiment_dataset, experiment_universe,
         manifest_universe, sealed_manifest_hash
    FROM experiments e
    LEFT JOIN market_data_coverage_manifests m
      ON m.dataset_version_id = e.dataset_version_id
    WHERE e.experiment_id = NEW.experiment_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'RESEARCH_RUN_EXPERIMENT_NOT_FOUND'
            USING ERRCODE = '23514';
    END IF;

    -- A missing manifest identifies generic research data. The stricter market
    -- provenance contract applies only to manifest-backed experiments.
    IF sealed_manifest_hash IS NULL THEN
        RETURN NEW;
    END IF;

    IF manifest_universe IS DISTINCT FROM experiment_universe THEN
        RAISE EXCEPTION 'RESEARCH_RUN_MARKET_DATA_UNIVERSE_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    INSERT INTO research_run_market_data_provenance(
        research_run_id,
        dataset_version_id,
        universe_version_id,
        manifest_hash
    ) VALUES (
        NEW.research_run_id,
        experiment_dataset,
        experiment_universe,
        sealed_manifest_hash
    );

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_research_run_market_data_provenance
AFTER INSERT ON research_runs
FOR EACH ROW EXECUTE FUNCTION bind_research_run_market_data_provenance();

CREATE OR REPLACE FUNCTION prevent_research_run_market_data_provenance_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'RESEARCH_RUN_MARKET_DATA_PROVENANCE_IMMUTABLE'
        USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_research_run_market_data_provenance_immutable
BEFORE UPDATE OR DELETE ON research_run_market_data_provenance
FOR EACH ROW EXECUTE FUNCTION prevent_research_run_market_data_provenance_mutation();
