-- HOPE v0.1 migration tamper detection.
-- Applied migrations are immutable artifacts: their recorded checksum must match
-- the migration file used to apply them.

ALTER TABLE hope_schema_migrations
    ADD COLUMN IF NOT EXISTS checksum CHAR(64);

-- Backfill checksums for migrations applied before checksum tracking existed.
UPDATE hope_schema_migrations SET checksum = '275ea702c90877ba5ba4c7f01973e59c7728e7bd5cb78bf5ab50b23962f2e0f4' WHERE version = '001_initial.sql' AND checksum IS NULL;
UPDATE hope_schema_migrations SET checksum = '2d8110be17b80573a3da26cc091137b829960546abb731121189a1cc904eb7b5' WHERE version = '002_integrity_constraints.sql' AND checksum IS NULL;
UPDATE hope_schema_migrations SET checksum = '8edcf0b8955f91f44dbf75d02f1f36f0c7d417b8de70ae16c9fbf676f87b8996' WHERE version = '003_experiment_invalidation_events.sql' AND checksum IS NULL;
UPDATE hope_schema_migrations SET checksum = 'fb4378dc1f5fae61da0ae40f6c18da7cdf0174c430403dd6f9ff3808aaacaebd' WHERE version = '004_identity_universe_integrity.sql' AND checksum IS NULL;
UPDATE hope_schema_migrations SET checksum = '99754f4f5440ee9673af593b4b11860357d821075766a829f27eecdd040540d6' WHERE version = '005_trading_integrity_guards.sql' AND checksum IS NULL;
UPDATE hope_schema_migrations SET checksum = 'aa489a4ec28bb2bba87e5ecd3990cc2ef54ad2fd7930abc269c4020452c5ad6e' WHERE version = '006_cross_entity_trading_integrity.sql' AND checksum IS NULL;
UPDATE hope_schema_migrations SET checksum = '8688dae0f7e71e16daf62aa5a30065c0c0374ad5d5afb1d7d53f86beba937e02' WHERE version = '007_identity_namespace_integrity.sql' AND checksum IS NULL;
