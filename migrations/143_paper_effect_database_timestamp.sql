-- PAPER effects are an authoritative side-effect audit ledger.  Preserve the
-- existing claim-lineage contract and require PostgreSQL's authenticated
-- transaction timestamp for every new effect.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM paper_effects WHERE created_at > clock_timestamp()
    ) THEN
        RAISE EXCEPTION 'PAPER_EFFECT_TIMESTAMP_IN_FUTURE'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_require_claimed_job_for_paper_effect()
RETURNS trigger AS $$
DECLARE
    run_status TEXT;
    run_created_at TIMESTAMPTZ;
BEGIN
    SELECT status, created_at INTO run_status, run_created_at
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

    IF NEW.created_at < run_created_at THEN
        RAISE EXCEPTION 'PAPER_EFFECT_PRECEDES_JOB_CLAIM'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.created_at > clock_timestamp() THEN
        RAISE EXCEPTION 'PAPER_EFFECT_TIMESTAMP_IN_FUTURE'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.created_at IS DISTINCT FROM transaction_timestamp() THEN
        RAISE EXCEPTION 'PAPER_EFFECT_TIMESTAMP_NOT_DATABASE_AUTHENTICATED'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
