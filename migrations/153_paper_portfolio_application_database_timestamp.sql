-- Applied-fill rows are immutable PAPER accounting evidence.  A durable FILL
-- effect identifies the authoritative repository path before an application is
-- inserted, allowing PostgreSQL to authenticate persistence time while legacy
-- corruption probes without effect lineage remain readable and fail closed.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM paper_portfolio_fill_applications application
          JOIN paper_effects effect
            ON effect.effect_type = 'FILL'
           AND effect.entity_id = application.fill_id
         WHERE application.applied_at > clock_timestamp()
    ) THEN
        RAISE EXCEPTION
            'PAPER_PORTFOLIO_APPLICATION_TIMESTAMP_IN_FUTURE'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION
hope_authenticate_paper_portfolio_application_timestamp()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM paper_effects
         WHERE effect_type = 'FILL'
           AND entity_id = NEW.fill_id
    ) THEN
        RETURN NEW;
    END IF;

    IF NEW.applied_at > clock_timestamp() THEN
        RAISE EXCEPTION
            'PAPER_PORTFOLIO_APPLICATION_TIMESTAMP_IN_FUTURE'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.applied_at IS DISTINCT FROM transaction_timestamp() THEN
        RAISE EXCEPTION
            'PAPER_PORTFOLIO_APPLICATION_TIMESTAMP_NOT_DATABASE_AUTHENTICATED'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_portfolio_application_database_timestamp
BEFORE INSERT ON paper_portfolio_fill_applications
FOR EACH ROW
EXECUTE FUNCTION hope_authenticate_paper_portfolio_application_timestamp();
