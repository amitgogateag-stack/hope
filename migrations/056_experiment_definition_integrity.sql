-- Experiment definitions are immutable historical facts, so invalid identity/lifecycle
-- values must be rejected at creation rather than becoming permanent provenance.
ALTER TABLE experiments
    ADD CONSTRAINT ck_experiments_id_nonblank
        CHECK (btrim(experiment_id) <> ''),
    ADD CONSTRAINT ck_experiments_hypothesis_nonblank
        CHECK (btrim(hypothesis) <> ''),
    ADD CONSTRAINT ck_experiments_status_created
        CHECK (status = 'CREATED');
