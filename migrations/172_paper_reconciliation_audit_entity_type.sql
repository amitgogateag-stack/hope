-- A reconciliation event is meaningful only for a durable JOB_RUN. Reject
-- alternate entity types before they can consume the immutable deterministic
-- receipt identity and block the legitimate no-replay receipt.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM audit_events
        WHERE event_type = 'PAPER_RUN_RECONCILED'
          AND entity_type IS DISTINCT FROM 'JOB_RUN'
    ) THEN
        RAISE EXCEPTION 'PAPER_RECONCILIATION_AUDIT_ENTITY_TYPE_INVALID'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION guard_paper_reconciliation_audit_entity_type()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.event_type = 'PAPER_RUN_RECONCILED'
       AND NEW.entity_type IS DISTINCT FROM 'JOB_RUN'
    THEN
        RAISE EXCEPTION 'PAPER_RECONCILIATION_AUDIT_ENTITY_TYPE_INVALID'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_paper_reconciliation_audit_entity_type ON audit_events;
CREATE TRIGGER trg_paper_reconciliation_audit_entity_type
BEFORE INSERT ON audit_events
FOR EACH ROW
EXECUTE FUNCTION guard_paper_reconciliation_audit_entity_type();
