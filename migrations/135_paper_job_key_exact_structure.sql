-- The registered job key is the final component of the durable PAPER identity.
-- Reject embedded separators so different logical tuples cannot collapse into
-- an ambiguous colon-delimited job key at the storage boundary.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM job_runs
         WHERE left(job_key, 6) = 'paper:'
           AND job_key !~ (
               '^paper:(USA|INDIA):'
               || '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-'
               || '[0-9a-f]{4}-[0-9a-f]{12}:[^:]+$'
           )
    ) THEN
        RAISE EXCEPTION 'PAPER_JOB_KEY_STRUCTURE_INVALID'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

ALTER TABLE job_runs
    DROP CONSTRAINT ck_job_runs_paper_job_key_structure;

ALTER TABLE job_runs
    ADD CONSTRAINT ck_job_runs_paper_job_key_structure
        CHECK (
            left(job_key, 6) <> 'paper:'
            OR job_key ~ (
                '^paper:(USA|INDIA):'
                || '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-'
                || '[0-9a-f]{4}-[0-9a-f]{12}:[^:]+$'
            )
        );
