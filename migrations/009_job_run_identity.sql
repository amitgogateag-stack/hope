-- Durable idempotency key for scheduled HOPE job invocations.
CREATE TABLE IF NOT EXISTS job_runs (
    job_run_id UUID PRIMARY KEY,
    job_key TEXT NOT NULL CHECK (btrim(job_key) <> ''),
    scheduled_for TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_job_runs_schedule UNIQUE (job_key, scheduled_for)
);
