-- Migration checksums are canonical SHA-256 hex digests.
-- Reject malformed historical provenance before enforcing the invariant.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM hope_schema_migrations
        WHERE checksum::text !~ '^[0-9a-f]{64}$'
    ) THEN
        RAISE EXCEPTION 'SCHEMA_MIGRATION_CHECKSUM_NOT_CANONICAL'
            USING ERRCODE='23514';
    END IF;
END;
$$;

ALTER TABLE hope_schema_migrations
    ADD CONSTRAINT ck_hope_schema_migrations_checksum_canonical
    CHECK (checksum::text ~ '^[0-9a-f]{64}$');
