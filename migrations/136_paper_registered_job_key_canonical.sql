-- The application rejects leading or trailing whitespace in a registered
-- PAPER job key.  Enforce the same canonical component boundary in storage so
-- direct SQL cannot create an alternate textual identity that bypasses the
-- application contract.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM job_runs
         WHERE left(job_key, 6) = 'paper:'
           AND split_part(job_key, ':', 4) ~
               '(^[[:space:]]|[[:space:]]$)'
    ) THEN
        RAISE EXCEPTION 'PAPER_REGISTERED_JOB_KEY_NOT_CANONICAL'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

ALTER TABLE job_runs
    ADD CONSTRAINT ck_job_runs_paper_registered_job_key_canonical
        CHECK (
            left(job_key, 6) <> 'paper:'
            OR split_part(job_key, ':', 4) !~
                '(^[[:space:]]|[[:space:]]$)'
        );
