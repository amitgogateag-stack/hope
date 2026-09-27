-- Bind autonomous PAPER rows to the authoritative durable key structure:
-- paper:{market}:{strategy_version_id}:{registered_job_key}.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM job_runs
         WHERE left(job_key, 6) = 'paper:'
           AND NOT (
               split_part(job_key, ':', 2) IN ('USA', 'INDIA')
               AND split_part(job_key, ':', 3) ~
                   '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
               AND split_part(job_key, ':', 4) <> ''
           )
    ) THEN
        RAISE EXCEPTION 'PAPER_JOB_KEY_STRUCTURE_INVALID'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

ALTER TABLE job_runs
    ADD CONSTRAINT ck_job_runs_paper_job_key_structure
        CHECK (
            left(job_key, 6) <> 'paper:'
            OR (
                split_part(job_key, ':', 2) IN ('USA', 'INDIA')
                AND split_part(job_key, ':', 3) ~
                    '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                AND split_part(job_key, ':', 4) <> ''
            )
        );
