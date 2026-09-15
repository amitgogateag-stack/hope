-- Serialize market-bar inserts with dataset-version sealing.
-- The finalizer locks the dataset_versions row FOR UPDATE while proving exact
-- coverage and sealing. Inserts must take a conflicting row lock so none can
-- commit after that proof against a stale immutable = FALSE observation.

CREATE OR REPLACE FUNCTION hope_guard_market_bar_write()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    version_immutable BOOLEAN;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT immutable
        INTO version_immutable
        FROM dataset_versions
        WHERE dataset_version_id = NEW.dataset_version_id
        FOR SHARE;

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
