-- Immutable PAPER risk decisions are execution audit evidence.  Require their
-- persistence timestamp to come from PostgreSQL while preserving source-signal
-- decision_time as the logical market-data timestamp.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM paper_risk_assessments
         WHERE created_at > clock_timestamp()
    ) THEN
        RAISE EXCEPTION 'PAPER_RISK_TIMESTAMP_IN_FUTURE'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_authenticate_paper_risk_timestamp()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.created_at > clock_timestamp() THEN
        RAISE EXCEPTION 'PAPER_RISK_TIMESTAMP_IN_FUTURE'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.created_at IS DISTINCT FROM transaction_timestamp() THEN
        RAISE EXCEPTION 'PAPER_RISK_TIMESTAMP_NOT_DATABASE_AUTHENTICATED'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_risk_database_timestamp
BEFORE INSERT ON paper_risk_assessments
FOR EACH ROW
EXECUTE FUNCTION hope_authenticate_paper_risk_timestamp();
