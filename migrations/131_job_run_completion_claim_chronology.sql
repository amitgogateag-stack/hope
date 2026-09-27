-- A durable job cannot complete before its claim was recorded.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM job_runs
         WHERE completed_at IS NOT NULL
           AND completed_at < created_at
    ) THEN
        RAISE EXCEPTION 'JOB_RUN_COMPLETION_PRECEDES_CLAIM'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

ALTER TABLE job_runs
    ADD CONSTRAINT ck_job_runs_completion_not_before_claim
        CHECK (completed_at IS NULL OR completed_at >= created_at);
