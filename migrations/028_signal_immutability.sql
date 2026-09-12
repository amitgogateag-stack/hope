-- Historical signal decisions are evidence and must never be rewritten.
CREATE OR REPLACE FUNCTION hope_reject_signal_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'SIGNAL_IMMUTABLE: signal history cannot be modified or deleted'
        USING ERRCODE = '23514';
END;
$$;

DROP TRIGGER IF EXISTS trg_signals_immutable ON signals;
CREATE TRIGGER trg_signals_immutable
BEFORE UPDATE OR DELETE ON signals
FOR EACH ROW EXECUTE FUNCTION hope_reject_signal_mutation();
