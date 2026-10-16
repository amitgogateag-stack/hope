-- Explicit stranded-claim quarantine must never invalidate recovery evidence.
-- UPDATE owns the job row lock. Effect insertion takes the same lock, so either
-- the evidence commits first and quarantine is rejected, or quarantine commits
-- first and the existing CLAIMED-owner effect guard rejects the writer.
CREATE FUNCTION hope_guard_paper_quarantine_evidence()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = 'FAILED'
       AND NEW.failure_code = 'PAPER_JOB_INCOMPLETE_PRIOR_CLAIM'
    THEN
        -- Locks do not refresh a repeatable snapshot when effect insertion
        -- locks, but does not update, the job tuple. Require fresh snapshots.
        IF current_setting('transaction_isolation') <> 'read committed' THEN
            RAISE EXCEPTION 'PAPER_JOB_QUARANTINE_REQUIRES_READ_COMMITTED'
                USING ERRCODE = '23514';
        END IF;
        IF EXISTS (SELECT 1 FROM paper_effects WHERE job_run_id = OLD.job_run_id) THEN
            RAISE EXCEPTION 'PAPER_JOB_QUARANTINE_REQUIRES_RECONCILIATION'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_quarantine_evidence_guard
BEFORE UPDATE OF status, failure_code ON job_runs
FOR EACH ROW EXECUTE FUNCTION hope_guard_paper_quarantine_evidence();
