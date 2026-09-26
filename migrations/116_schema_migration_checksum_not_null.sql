-- Applied migration checksums are mandatory provenance.
-- Fail closed if any historical migration lacks a checksum, then make the
-- invariant database-enforced so later direct SQL cannot create unverifiable rows.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM hope_schema_migrations
        WHERE checksum IS NULL
    ) THEN
        RAISE EXCEPTION 'SCHEMA_MIGRATION_CHECKSUM_REQUIRED'
            USING ERRCODE='23514';
    END IF;
END;
$$;

ALTER TABLE hope_schema_migrations
    ALTER COLUMN checksum SET NOT NULL;
