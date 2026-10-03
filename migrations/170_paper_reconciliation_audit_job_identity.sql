-- Authenticate PAPER reconciliation audit receipts against durable job identity.
-- Existing rows must already identify a real PAPER job run before this guard is installed.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM audit_events a
        LEFT JOIN job_runs j
          ON j.job_run_id::text = a.entity_id
        WHERE a.event_type = 'PAPER_RUN_RECONCILED'
          AND a.entity_type = 'JOB_RUN'
          AND (
              j.job_run_id IS NULL
              OR left(j.job_key, 6) <> 'paper:'
          )
    ) THEN
        RAISE EXCEPTION 'PAPER_RECONCILIATION_AUDIT_JOB_IDENTITY_INVALID'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION guard_paper_reconciliation_audit_job_identity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.event_type = 'PAPER_RUN_RECONCILED'
       AND NEW.entity_type = 'JOB_RUN'
    THEN
        IF NEW.entity_id !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
            RAISE EXCEPTION 'PAPER_RECONCILIATION_AUDIT_JOB_IDENTITY_INVALID'
                USING ERRCODE = '23514';
        END IF;

        IF NOT EXISTS (
            SELECT 1
            FROM job_runs j
            WHERE j.job_run_id::text = NEW.entity_id
              AND left(j.job_key, 6) = 'paper:'
        ) THEN
            RAISE EXCEPTION 'PAPER_RECONCILIATION_AUDIT_JOB_IDENTITY_INVALID'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_paper_reconciliation_audit_job_identity ON audit_events;
CREATE TRIGGER trg_paper_reconciliation_audit_job_identity
BEFORE INSERT ON audit_events
FOR EACH ROW
EXECUTE FUNCTION guard_paper_reconciliation_audit_job_identity();
