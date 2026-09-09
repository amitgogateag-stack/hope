-- Durable PAPER side-effect lineage and idempotency boundary.
CREATE TABLE paper_effects (
    effect_id UUID PRIMARY KEY,
    job_run_id UUID NOT NULL REFERENCES job_runs(job_run_id),
    effect_type TEXT NOT NULL CHECK (effect_type IN ('SIGNAL','ORDER','FILL','PNL')),
    entity_id UUID NOT NULL,
    payload_hash CHAR(64) NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_paper_effects_logical UNIQUE (effect_type, entity_id)
);

CREATE OR REPLACE FUNCTION hope_require_claimed_job_for_paper_effect()
RETURNS trigger AS $$
DECLARE
    run_status TEXT;
BEGIN
    SELECT status INTO run_status
    FROM job_runs
    WHERE job_run_id = NEW.job_run_id;

    IF run_status IS DISTINCT FROM 'CLAIMED' THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = concat(
                'PAPER_EFFECT_REQUIRES_CLAIMED_JOB: job ',
                NEW.job_run_id,
                ' has status ',
                coalesce(run_status, '<missing>')
            );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_paper_effect_requires_claimed_job
BEFORE INSERT ON paper_effects
FOR EACH ROW EXECUTE FUNCTION hope_require_claimed_job_for_paper_effect();
