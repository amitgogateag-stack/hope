-- HOPE market-data immutability guardrails.
-- Market bars may be appended only while a dataset version is explicitly staging (immutable = FALSE).
-- Once sealed immutable, neither its metadata nor its market-data history may be reopened or changed.

CREATE OR REPLACE FUNCTION hope_guard_market_bar_write()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    version_immutable BOOLEAN;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT immutable
        INTO version_immutable
        FROM dataset_versions
        WHERE dataset_version_id = NEW.dataset_version_id;

        IF version_immutable IS TRUE THEN
            RAISE EXCEPTION 'MARKET_BAR_IMMUTABLE_DATASET_VERSION: dataset version %% is sealed', NEW.dataset_version_id
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    RAISE EXCEPTION 'MARKET_BAR_HISTORY_IMMUTABLE: market bars are append-only'
        USING ERRCODE = '23514';
END;
$$;

DROP TRIGGER IF EXISTS trg_market_bars_immutable ON market_bars;
CREATE TRIGGER trg_market_bars_immutable
BEFORE INSERT OR UPDATE OR DELETE ON market_bars
FOR EACH ROW EXECUTE FUNCTION hope_guard_market_bar_write();

CREATE OR REPLACE FUNCTION hope_guard_dataset_version_seal()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.immutable IS TRUE THEN
            RAISE EXCEPTION 'DATASET_VERSION_IMMUTABLE: sealed dataset version %% cannot be deleted', OLD.dataset_version_id
                USING ERRCODE = '23514';
        END IF;
        RETURN OLD;
    END IF;

    IF OLD.immutable IS TRUE THEN
        RAISE EXCEPTION 'DATASET_VERSION_IMMUTABLE: sealed dataset version %% cannot be modified or reopened', OLD.dataset_version_id
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_dataset_versions_sealed ON dataset_versions;
CREATE TRIGGER trg_dataset_versions_sealed
BEFORE UPDATE OR DELETE ON dataset_versions
FOR EACH ROW EXECUTE FUNCTION hope_guard_dataset_version_seal();
