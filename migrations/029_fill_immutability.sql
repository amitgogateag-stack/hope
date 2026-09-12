-- Persisted fills are execution facts and must remain append-only.

CREATE OR REPLACE FUNCTION hope_reject_fill_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'FILL_IMMUTABLE: fill history cannot be modified or deleted'
        USING ERRCODE = '23514';
END;
$$;

DROP TRIGGER IF EXISTS trg_fills_immutable ON fills;
CREATE TRIGGER trg_fills_immutable
BEFORE UPDATE OR DELETE ON fills
FOR EACH ROW EXECUTE FUNCTION hope_reject_fill_mutation();
