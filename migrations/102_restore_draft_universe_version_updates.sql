-- Migration 101 strengthened experiment-referenced version immutability but
-- returned OLD for every UPDATE, silently suppressing legitimate draft edits.
-- Preserve the frozen provenance boundary while allowing unreferenced drafts
-- to evolve until first experiment use.
CREATE OR REPLACE FUNCTION hope_reject_referenced_universe_version_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM experiments
        WHERE universe_version_id = OLD.universe_version_id
    ) THEN
        RAISE EXCEPTION 'EXPERIMENT_UNIVERSE_IMMUTABLE: referenced universe version cannot be modified or deleted'
            USING ERRCODE = '23514';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;
