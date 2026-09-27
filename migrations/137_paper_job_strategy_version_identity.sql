-- Autonomous PAPER identity embeds the immutable strategy-version UUID.
-- Reject claims for phantom strategy versions so direct SQL cannot create work
-- outside HOPE's durable strategy provenance graph.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM job_runs job
          LEFT JOIN strategy_versions version
            ON version.strategy_version_id =
               split_part(job.job_key, ':', 3)::UUID
         WHERE left(job.job_key, 6) = 'paper:'
           AND version.strategy_version_id IS NULL
    ) THEN
        RAISE EXCEPTION 'PAPER_JOB_CLAIM_STRATEGY_VERSION_NOT_FOUND'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION guard_paper_job_claim_environment()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    current_state TEXT;
BEGIN
    IF left(NEW.job_key, 6) <> 'paper:' THEN
        RETURN NEW;
    END IF;

    IF NEW.status IS DISTINCT FROM 'CLAIMED'
       OR NEW.completed_at IS NOT NULL
       OR NEW.failure_code IS NOT NULL
    THEN
        RAISE EXCEPTION 'PAPER_JOB_INSERT_REQUIRES_CLAIMED_STATE'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.scheduled_for > clock_timestamp() THEN
        RAISE EXCEPTION 'PAPER_JOB_CLAIM_SCHEDULED_IN_FUTURE'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.created_at > clock_timestamp() THEN
        RAISE EXCEPTION 'PAPER_JOB_CLAIM_TIMESTAMP_IN_FUTURE'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.created_at < NEW.scheduled_for THEN
        RAISE EXCEPTION 'PAPER_JOB_CLAIM_PRECEDES_SCHEDULE'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.created_at IS DISTINCT FROM transaction_timestamp() THEN
        RAISE EXCEPTION 'PAPER_JOB_CLAIM_TIMESTAMP_NOT_DATABASE_AUTHENTICATED'
            USING ERRCODE = '23514';
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtext('hope:paper:environment-control')::bigint
    );

    SELECT state
      INTO current_state
      FROM paper_environment_control_events
     ORDER BY control_sequence DESC
     LIMIT 1;

    IF current_state IS DISTINCT FROM 'RUNNING' THEN
        RAISE EXCEPTION 'PAPER_JOB_CLAIM_REQUIRES_RUNNING_ENVIRONMENT'
            USING ERRCODE = '23514';
    END IF;

    -- Earlier constraints own malformed-key error identity.  Only parse the
    -- UUID after the key satisfies the complete canonical structure.
    IF NEW.job_key ~ (
           '^paper:(USA|INDIA):'
           || '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-'
           || '[0-9a-f]{4}-[0-9a-f]{12}:[^:]+$'
       )
       AND split_part(NEW.job_key, ':', 4) !~
           '(^[[:space:]]|[[:space:]]$)'
       AND NOT EXISTS (
           SELECT 1
             FROM strategy_versions
            WHERE strategy_version_id =
                  split_part(NEW.job_key, ':', 3)::UUID
       )
    THEN
        RAISE EXCEPTION 'PAPER_JOB_CLAIM_STRATEGY_VERSION_NOT_FOUND'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;
