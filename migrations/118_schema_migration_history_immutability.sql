-- Applied migration history is append-only provenance.
-- The migration runner may insert future versions, but recorded versions and
-- checksums must never be rewritten or deleted through direct SQL.
CREATE OR REPLACE FUNCTION guard_schema_migration_history_immutability()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'SCHEMA_MIGRATION_HISTORY_IMMUTABLE'
        USING ERRCODE='23514';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_schema_migration_history_immutability
    ON hope_schema_migrations;

CREATE TRIGGER trg_schema_migration_history_immutability
BEFORE UPDATE OR DELETE ON hope_schema_migrations
FOR EACH ROW
EXECUTE FUNCTION guard_schema_migration_history_immutability();
