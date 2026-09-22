-- Configuration snapshots are content-addressed provenance.
-- Enforce canonical SHA-256 hash representation at the PostgreSQL boundary.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM configuration_snapshots
        WHERE configuration_hash !~ '^[0-9a-f]{64}$'
    ) THEN
        RAISE EXCEPTION 'CONFIGURATION_HASH_NOT_CANONICAL: existing configuration snapshot hash is not lowercase SHA-256'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

ALTER TABLE configuration_snapshots
    ADD CONSTRAINT ck_configuration_snapshots_hash_canonical
    CHECK (configuration_hash ~ '^[0-9a-f]{64}$');
