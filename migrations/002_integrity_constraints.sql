-- HOPE v0.1 integrity hardening.
-- This migration deliberately adds database-enforced guardrails rather than relying only on application code.

-- An evaluated/active source symbol cannot share a broker identity with another active mapping.
CREATE UNIQUE INDEX IF NOT EXISTS uq_identity_active_broker_instrument
    ON identity_mappings (broker_instrument_id)
    WHERE status = 'ACTIVE';

-- Historical experiment records are append-only in intent. These triggers prevent accidental mutation/deletion.
CREATE OR REPLACE FUNCTION hope_reject_experiment_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'HOPE experiment history is immutable; invalidate rather than mutate/delete';
END;
$$;

DROP TRIGGER IF EXISTS trg_experiments_immutable ON experiments;
CREATE TRIGGER trg_experiments_immutable
BEFORE UPDATE OR DELETE ON experiments
FOR EACH ROW EXECUTE FUNCTION hope_reject_experiment_mutation();

CREATE OR REPLACE FUNCTION hope_reject_pnl_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'HOPE P&L history is immutable';
END;
$$;

DROP TRIGGER IF EXISTS trg_pnl_events_immutable ON pnl_events;
CREATE TRIGGER trg_pnl_events_immutable
BEFORE UPDATE OR DELETE ON pnl_events
FOR EACH ROW EXECUTE FUNCTION hope_reject_pnl_mutation();
