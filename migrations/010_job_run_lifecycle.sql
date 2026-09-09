-- Durable terminal lifecycle state for claimed scheduled job runs.
ALTER TABLE job_runs
    ADD COLUMN status TEXT NOT NULL DEFAULT 'CLAIMED',
    ADD COLUMN completed_at TIMESTAMPTZ,
    ADD COLUMN failure_code TEXT;

ALTER TABLE job_runs
    ADD CONSTRAINT ck_job_runs_status
        CHECK (status IN ('CLAIMED','SUCCEEDED','FAILED')),
    ADD CONSTRAINT ck_job_runs_terminal_shape
        CHECK (
            (status = 'CLAIMED' AND completed_at IS NULL AND failure_code IS NULL)
            OR
            (status = 'SUCCEEDED' AND completed_at IS NOT NULL AND failure_code IS NULL)
            OR
            (
                status = 'FAILED'
                AND completed_at IS NOT NULL
                AND failure_code IS NOT NULL
                AND btrim(failure_code) <> ''
            )
        ),
    ADD CONSTRAINT ck_job_runs_completion_not_before_schedule
        CHECK (completed_at IS NULL OR completed_at >= scheduled_for);
