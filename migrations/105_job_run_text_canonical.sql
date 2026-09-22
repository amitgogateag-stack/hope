-- Preserve scheduler idempotency by rejecting alternate textual identities.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM job_runs
        WHERE job_key <> btrim(job_key)
           OR (failure_code IS NOT NULL AND failure_code <> btrim(failure_code))
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'JOB_RUN_TEXT_NOT_CANONICAL';
    END IF;
END;
$$;

ALTER TABLE job_runs
    ADD CONSTRAINT ck_job_runs_job_key_canonical
    CHECK (job_key = btrim(job_key)),
    ADD CONSTRAINT ck_job_runs_failure_code_canonical
    CHECK (failure_code IS NULL OR failure_code = btrim(failure_code));
