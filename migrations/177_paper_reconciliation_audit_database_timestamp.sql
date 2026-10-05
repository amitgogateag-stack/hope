-- Reconciliation receipts are authoritative no-replay audit evidence.
-- Require PostgreSQL's transaction timestamp so direct SQL cannot backdate or
-- otherwise forge when a proven-complete PAPER run was acknowledged.
CREATE OR REPLACE FUNCTION guard_paper_reconciliation_audit_timestamp()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.event_type = 'PAPER_RUN_RECONCILED'
       AND NEW.created_at IS DISTINCT FROM transaction_timestamp()
    THEN
        RAISE EXCEPTION 'PAPER_RECONCILIATION_AUDIT_TIMESTAMP_NOT_DATABASE_AUTHENTICATED'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_paper_reconciliation_audit_timestamp ON audit_events;
CREATE TRIGGER trg_paper_reconciliation_audit_timestamp
BEFORE INSERT ON audit_events
FOR EACH ROW
EXECUTE FUNCTION guard_paper_reconciliation_audit_timestamp();
