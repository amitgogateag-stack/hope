-- A reconciliation receipt is evidence that an interrupted PAPER run was proven
-- complete and terminalized without replay. The database must not accept that
-- evidence for a run that is still CLAIMED or terminalized as anything but a
-- clean SUCCEEDED run.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM audit_events a
        JOIN job_runs j
          ON j.job_run_id::text = a.entity_id
        WHERE a.event_type = 'PAPER_RUN_RECONCILED'
          AND a.entity_type = 'JOB_RUN'
          AND (
              j.status <> 'SUCCEEDED'
              OR j.completed_at IS NULL
              OR j.failure_code IS NOT NULL
          )
    ) THEN
        RAISE EXCEPTION 'PAPER_RECONCILIATION_AUDIT_TERMINAL_TRUTH_INVALID'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION guard_paper_reconciliation_audit_terminal_truth()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    durable_status TEXT;
    durable_completed_at TIMESTAMPTZ;
    durable_failure_code TEXT;
BEGIN
    IF NEW.event_type = 'PAPER_RUN_RECONCILED'
       AND NEW.entity_type = 'JOB_RUN'
    THEN
        SELECT status, completed_at, failure_code
          INTO durable_status, durable_completed_at, durable_failure_code
          FROM job_runs
         WHERE job_run_id::text = NEW.entity_id;

        IF durable_status IS DISTINCT FROM 'SUCCEEDED'
           OR durable_completed_at IS NULL
           OR durable_failure_code IS NOT NULL
        THEN
            RAISE EXCEPTION 'PAPER_RECONCILIATION_AUDIT_TERMINAL_TRUTH_INVALID'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_paper_reconciliation_audit_terminal_truth ON audit_events;
CREATE TRIGGER trg_paper_reconciliation_audit_terminal_truth
BEFORE INSERT ON audit_events
FOR EACH ROW
EXECUTE FUNCTION guard_paper_reconciliation_audit_terminal_truth();
