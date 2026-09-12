-- Configuration snapshots are content-addressed experiment provenance and must be append-only.
-- Mutating canonical_json under an existing hash would make historical experiments non-reproducible.

CREATE OR REPLACE FUNCTION hope_reject_configuration_snapshot_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'CONFIGURATION_SNAPSHOT_IMMUTABLE: configuration snapshots cannot be modified or deleted'
        USING ERRCODE = '23514';
END;
$$;

DROP TRIGGER IF EXISTS trg_configuration_snapshots_immutable ON configuration_snapshots;
CREATE TRIGGER trg_configuration_snapshots_immutable
BEFORE UPDATE OR DELETE ON configuration_snapshots
FOR EACH ROW EXECUTE FUNCTION hope_reject_configuration_snapshot_mutation();
