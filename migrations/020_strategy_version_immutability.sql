-- Strategy versions are durable experiment provenance and must be append-only.
-- Changing version or code_commit in place would rewrite the strategy identity of historical experiments.

CREATE OR REPLACE FUNCTION hope_reject_strategy_version_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'STRATEGY_VERSION_IMMUTABLE: strategy versions cannot be modified or deleted'
        USING ERRCODE = '23514';
END;
$$;

DROP TRIGGER IF EXISTS trg_strategy_versions_immutable ON strategy_versions;
CREATE TRIGGER trg_strategy_versions_immutable
BEFORE UPDATE OR DELETE ON strategy_versions
FOR EACH ROW EXECUTE FUNCTION hope_reject_strategy_version_mutation();
