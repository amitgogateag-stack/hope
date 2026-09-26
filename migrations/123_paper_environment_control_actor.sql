-- PAPER environment control transitions require attributable provenance.
-- Historical rows are attributed to the database principal that applies this migration;
-- future direct SQL defaults to its database principal while application transitions
-- supply an explicit actor.
ALTER TABLE paper_environment_control_events
    ADD COLUMN actor TEXT NOT NULL DEFAULT current_user;

ALTER TABLE paper_environment_control_events
    ADD CONSTRAINT ck_paper_environment_control_actor_canonical
    CHECK (length(btrim(actor)) > 0 AND actor = btrim(actor));
