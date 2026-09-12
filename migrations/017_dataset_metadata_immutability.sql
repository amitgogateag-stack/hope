-- Dataset metadata becomes historical provenance once any version is sealed.
-- Staging-only datasets may still be corrected before sealing, but a dataset that
-- backs immutable history must not have its declared identity/source/PIT status
-- rewritten or deleted afterward.

CREATE OR REPLACE FUNCTION hope_guard_sealed_dataset_metadata()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM dataset_versions
        WHERE dataset_id = OLD.dataset_id
          AND immutable IS TRUE
    ) THEN
        RAISE EXCEPTION 'DATASET_METADATA_IMMUTABLE: dataset % backs a sealed version', OLD.dataset_id
            USING ERRCODE = '23514';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_datasets_sealed_metadata ON datasets;
CREATE TRIGGER trg_datasets_sealed_metadata
BEFORE UPDATE OR DELETE ON datasets
FOR EACH ROW EXECUTE FUNCTION hope_guard_sealed_dataset_metadata();
