-- Experiments that reference manifest-backed market data must preserve the same
-- certified PIT provenance that was sealed with the dataset version.  The
-- experiment row is immutable, so a dataset/universe disagreement must be
-- rejected at admission rather than discovered after research results exist.

CREATE OR REPLACE FUNCTION hope_guard_experiment_market_data_provenance()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    dataset_immutable BOOLEAN;
    dataset_vintage TEXT;
    dataset_pit BOOLEAN;
    manifest_universe UUID;
    experiment_universe_pit BOOLEAN;
    declared_count INTEGER;
    actual_count INTEGER;
BEGIN
    SELECT dv.immutable, dv.vintage_label, d.pit_certified, m.universe_version_id
    INTO dataset_immutable, dataset_vintage, dataset_pit, manifest_universe
    FROM dataset_versions dv
    JOIN datasets d ON d.dataset_id = dv.dataset_id
    JOIN market_data_coverage_manifests m
      ON m.dataset_version_id = dv.dataset_version_id
    WHERE dv.dataset_version_id = NEW.dataset_version_id;

    -- Non-market-data experiment datasets remain governed by the generic
    -- experiment freeze/provenance contracts.  A durable market-data manifest
    -- is the discriminator for this stricter boundary.
    IF NOT FOUND THEN
        RETURN NEW;
    END IF;

    IF dataset_immutable IS DISTINCT FROM TRUE
       OR dataset_vintage IS DISTINCT FROM 'sealed'
       OR dataset_pit IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'EXPERIMENT_MARKET_DATA_NOT_CERTIFIED: manifest-backed market data must be sealed and PIT-certified'
            USING ERRCODE = '23514';
    END IF;

    IF manifest_universe IS DISTINCT FROM NEW.universe_version_id THEN
        RAISE EXCEPTION 'EXPERIMENT_MARKET_DATA_UNIVERSE_MISMATCH: experiment universe must equal the dataset manifest universe'
            USING ERRCODE = '23514';
    END IF;

    SELECT pit_certified, declared_member_count
    INTO experiment_universe_pit, declared_count
    FROM universe_versions
    WHERE universe_version_id = NEW.universe_version_id;

    IF NOT FOUND THEN
        RETURN NEW;
    END IF;

    SELECT COUNT(*)::integer
    INTO actual_count
    FROM universe_members
    WHERE universe_version_id = NEW.universe_version_id;

    IF experiment_universe_pit IS DISTINCT FROM TRUE
       OR actual_count IS DISTINCT FROM declared_count THEN
        RAISE EXCEPTION 'EXPERIMENT_MARKET_DATA_UNIVERSE_NOT_CERTIFIED: experiment universe must be PIT-certified with exact cardinality'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_experiment_market_data_provenance ON experiments;
CREATE TRIGGER trg_experiment_market_data_provenance
BEFORE INSERT ON experiments
FOR EACH ROW EXECUTE FUNCTION hope_guard_experiment_market_data_provenance();
