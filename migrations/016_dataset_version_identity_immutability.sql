-- Dataset-version identity is immutable from creation, including while staging.
-- Staging may add market bars, adjust non-identity metadata, and seal once, but it
-- must never move to a different dataset or version identity before sealing.

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

    IF NEW.dataset_version_id IS DISTINCT FROM OLD.dataset_version_id
       OR NEW.dataset_id IS DISTINCT FROM OLD.dataset_id
       OR NEW.version IS DISTINCT FROM OLD.version THEN
        RAISE EXCEPTION 'DATASET_VERSION_IDENTITY_IMMUTABLE: dataset version identity cannot be changed'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;
