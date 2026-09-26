-- Global PAPER environment kill switch.
-- State transitions are append-only events so every halt/resume remains auditable.
CREATE TABLE paper_environment_control_events (
    control_sequence BIGSERIAL PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('RUNNING','HALTED')),
    reason TEXT NOT NULL CHECK (length(btrim(reason)) > 0 AND reason = btrim(reason)),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO paper_environment_control_events(state, reason)
VALUES ('RUNNING', 'INITIAL_PAPER_ENVIRONMENT_STATE');

CREATE OR REPLACE FUNCTION hope_reject_paper_environment_control_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '23514',
        MESSAGE = 'PAPER_ENVIRONMENT_CONTROL_IMMUTABLE';
END;
$$;

CREATE TRIGGER trg_paper_environment_control_immutable
BEFORE UPDATE OR DELETE ON paper_environment_control_events
FOR EACH ROW EXECUTE FUNCTION hope_reject_paper_environment_control_mutation();
