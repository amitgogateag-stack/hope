-- Extend certified evidence lineage from v2 run identity to v3 exact market-data identity.
CREATE OR REPLACE FUNCTION guard_certified_research_run_evidence_identity()
RETURNS trigger AS $$
DECLARE
    evidence_schema TEXT;
    parent_experiment_id TEXT;
    parent_run_fingerprint CHAR(64);
    parent_dataset_version_id UUID;
    parent_universe_version_id UUID;
    parent_manifest_hash CHAR(64);
    research_provenance JSONB;
BEGIN
    evidence_schema := NEW.canonical_result->>'schema';

    -- Generic evidence keeps its existing contract. Certified v2 remains
    -- recognizable as legacy evidence; v3 adds exact sealed market identity.
    IF evidence_schema IS NULL OR evidence_schema NOT IN (
        'hope.certified-backtest-result.v2',
        'hope.certified-backtest-result.v3'
    ) THEN
        RETURN NEW;
    END IF;

    IF jsonb_typeof(NEW.canonical_result) <> 'object'
       OR NOT (NEW.canonical_result ? 'execution_provenance')
       OR NOT (NEW.canonical_result ? 'research_provenance')
       OR NOT (NEW.canonical_result ? 'backtest') THEN
        RAISE EXCEPTION 'RESEARCH_RUN_CERTIFIED_EVIDENCE_STRUCTURE_INVALID'
            USING ERRCODE = '23514';
    END IF;

    research_provenance := NEW.canonical_result->'research_provenance';
    IF research_provenance IS NULL
       OR jsonb_typeof(research_provenance) <> 'object'
       OR research_provenance->>'experiment_id' IS NULL
       OR research_provenance->>'run_fingerprint' IS NULL THEN
        RAISE EXCEPTION 'RESEARCH_RUN_CERTIFIED_EVIDENCE_PROVENANCE_REQUIRED'
            USING ERRCODE = '23514';
    END IF;

    SELECT rr.experiment_id, rr.run_fingerprint
    INTO parent_experiment_id, parent_run_fingerprint
    FROM research_runs rr
    WHERE rr.research_run_id = NEW.research_run_id;

    IF parent_experiment_id IS NULL THEN
        RAISE EXCEPTION 'RESEARCH_RUN_CERTIFIED_EVIDENCE_PARENT_RUN_MISSING'
            USING ERRCODE = '23503';
    END IF;

    IF research_provenance->>'experiment_id' IS DISTINCT FROM parent_experiment_id THEN
        RAISE EXCEPTION 'RESEARCH_RUN_CERTIFIED_EVIDENCE_EXPERIMENT_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    IF research_provenance->>'run_fingerprint' IS DISTINCT FROM parent_run_fingerprint::text THEN
        RAISE EXCEPTION 'RESEARCH_RUN_CERTIFIED_EVIDENCE_RUN_FINGERPRINT_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    IF evidence_schema = 'hope.certified-backtest-result.v3' THEN
        IF research_provenance->>'dataset_version_id' IS NULL
           OR research_provenance->>'universe_version_id' IS NULL
           OR research_provenance->>'market_data_manifest_hash' IS NULL THEN
            RAISE EXCEPTION 'RESEARCH_RUN_CERTIFIED_EVIDENCE_MARKET_DATA_PROVENANCE_REQUIRED'
                USING ERRCODE = '23514';
        END IF;

        SELECT dataset_version_id, universe_version_id, manifest_hash
        INTO parent_dataset_version_id, parent_universe_version_id, parent_manifest_hash
        FROM research_run_market_data_provenance
        WHERE research_run_id = NEW.research_run_id;

        IF parent_manifest_hash IS NULL THEN
            RAISE EXCEPTION 'RESEARCH_RUN_CERTIFIED_EVIDENCE_PARENT_MARKET_DATA_PROVENANCE_MISSING'
                USING ERRCODE = '23514';
        END IF;

        IF research_provenance->>'dataset_version_id' IS DISTINCT FROM parent_dataset_version_id::text THEN
            RAISE EXCEPTION 'RESEARCH_RUN_CERTIFIED_EVIDENCE_DATASET_VERSION_MISMATCH'
                USING ERRCODE = '23514';
        END IF;

        IF research_provenance->>'universe_version_id' IS DISTINCT FROM parent_universe_version_id::text THEN
            RAISE EXCEPTION 'RESEARCH_RUN_CERTIFIED_EVIDENCE_UNIVERSE_VERSION_MISMATCH'
                USING ERRCODE = '23514';
        END IF;

        IF research_provenance->>'market_data_manifest_hash' IS DISTINCT FROM parent_manifest_hash::text THEN
            RAISE EXCEPTION 'RESEARCH_RUN_CERTIFIED_EVIDENCE_MANIFEST_HASH_MISMATCH'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
