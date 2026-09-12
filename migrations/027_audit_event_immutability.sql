-- HOPE v0.1: audit history is append-only.
-- Audit events are historical evidence and must never be rewritten or deleted.

CREATE OR REPLACE FUNCTION hope_reject_audit_event_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '23514',
        MESSAGE = 'AUDIT_EVENT_IMMUTABLE: audit events cannot be modified or deleted';
END;
$$;

DROP TRIGGER IF EXISTS trg_audit_events_immutable ON audit_events;
CREATE TRIGGER trg_audit_events_immutable
BEFORE UPDATE OR DELETE ON audit_events
FOR EACH ROW EXECUTE FUNCTION hope_reject_audit_event_mutation();
