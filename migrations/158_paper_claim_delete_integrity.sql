-- Preserve durable PAPER scheduler claims against deletion.
--
-- A claimed run is the idempotency record for one scheduled execution. Deleting
-- it would allow the same PAPER schedule to be claimed and executed again.
-- Terminal rows remain governed by the earlier terminal-immutability trigger.

CREATE OR REPLACE FUNCTION hope_forbid_claimed_paper_job_delete()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.status = 'CLAIMED'
       AND left(OLD.job_key, 6) = 'paper:' THEN
        RAISE EXCEPTION 'PAPER_JOB_RUN_DELETE_FORBIDDEN'
            USING ERRCODE = '23514';
    END IF;

    RETURN OLD;
END;
$$;

DROP TRIGGER IF EXISTS trg_paper_job_run_delete_forbidden ON job_runs;
CREATE TRIGGER trg_paper_job_run_delete_forbidden
BEFORE DELETE ON job_runs
FOR EACH ROW
EXECUTE FUNCTION hope_forbid_claimed_paper_job_delete();
