-- HOPE v0.1 identity and universe integrity hardening.
-- Canonical broker identity is unique among active broker records.
CREATE UNIQUE INDEX IF NOT EXISTS uq_broker_active_instrument
    ON broker_instruments (instrument_id)
    WHERE status = 'ACTIVE' AND instrument_id IS NOT NULL;

-- A source symbol may not have two simultaneously active mappings.
CREATE UNIQUE INDEX IF NOT EXISTS uq_identity_active_source_symbol
    ON identity_mappings (source_symbol)
    WHERE status = 'ACTIVE';

-- Published universe membership is immutable.
CREATE OR REPLACE FUNCTION hope_reject_universe_member_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'HOPE published universe membership is immutable';
END;
$$;

DROP TRIGGER IF EXISTS trg_universe_members_immutable ON universe_members;
CREATE TRIGGER trg_universe_members_immutable
BEFORE UPDATE OR DELETE ON universe_members
FOR EACH ROW EXECUTE FUNCTION hope_reject_universe_member_mutation();
