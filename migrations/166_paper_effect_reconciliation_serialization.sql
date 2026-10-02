-- Serialize PAPER effect creation with reconciliation terminalization.
-- Reconciliation locks the job row before proving durable effects. Taking the same
-- row lock here prevents an effect from being inserted after proof but before the
-- run is changed from CLAIMED to a terminal state.
CREATE OR REPLACE FUNCTION hope_require_claimed_job_for_paper_effect()
RETURNS trigger AS $$
DECLARE
    run_status TEXT;
    run_created_at TIMESTAMPTZ;
BEGIN
    SELECT status, created_at INTO run_status, run_created_at
    FROM job_runs
    WHERE job_run_id = NEW.job_run_id
    FOR UPDATE;

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

    IF NEW.created_at < run_created_at THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'PAPER_EFFECT_PRECEDES_JOB_CLAIM';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
