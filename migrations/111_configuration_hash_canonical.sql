-- Configuration snapshots are content-addressed provenance.
-- CHAR(64) silently pads/truncates representation semantics, so convert the
-- hash key and its experiment foreign key to TEXT before enforcing canonical
-- lowercase SHA-256 storage.

ALTER TABLE experiments
    DROP CONSTRAINT IF EXISTS experiments_configuration_hash_fkey;

ALTER TABLE configuration_snapshots
    ALTER COLUMN configuration_hash TYPE TEXT
    USING configuration_hash::text;

ALTER TABLE experiments
    ALTER COLUMN configuration_hash TYPE TEXT
    USING configuration_hash::text;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM configuration_snapshots
        WHERE configuration_hash !~ '^[0-9a-f]{64}$'
           OR octet_length(configuration_hash) <> 64
    ) THEN
        RAISE EXCEPTION 'CONFIGURATION_HASH_NOT_CANONICAL: existing configuration snapshot hash is not lowercase SHA-256'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

ALTER TABLE configuration_snapshots
    ADD CONSTRAINT ck_configuration_snapshots_hash_canonical
    CHECK (
        configuration_hash ~ '^[0-9a-f]{64}$'
        AND octet_length(configuration_hash) = 64
    );

ALTER TABLE experiments
    ADD CONSTRAINT experiments_configuration_hash_fkey
    FOREIGN KEY (configuration_hash)
    REFERENCES configuration_snapshots(configuration_hash);
