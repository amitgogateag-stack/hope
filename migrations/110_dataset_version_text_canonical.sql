-- Dataset version and vintage label are durable market-data provenance text.
-- Reject blank or padded representations at the PostgreSQL boundary.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM dataset_versions
        WHERE version = ''
           OR version <> btrim(version)
           OR vintage_label = ''
           OR vintage_label <> btrim(vintage_label)
    ) THEN
        RAISE EXCEPTION 'DATASET_VERSION_TEXT_NOT_CANONICAL: existing dataset version provenance is blank or padded'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

ALTER TABLE dataset_versions
    ADD CONSTRAINT ck_dataset_versions_version_canonical
    CHECK (version <> '' AND version = btrim(version));

ALTER TABLE dataset_versions
    ADD CONSTRAINT ck_dataset_versions_vintage_label_canonical
    CHECK (vintage_label <> '' AND vintage_label = btrim(vintage_label));
