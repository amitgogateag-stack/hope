-- A portfolio version is the durable count of applied fills.  The repository
-- updates the portfolio and inserts the application in one transaction, so a
-- deferred constraint can reject either half committing on its own without
-- constraining the safe statement order inside that transaction.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM paper_portfolios portfolio
          LEFT JOIN LATERAL (
              SELECT count(*) AS application_count,
                     coalesce(max(application_sequence), 0) AS maximum_sequence
                FROM paper_portfolio_fill_applications application
               WHERE application.portfolio_id = portfolio.portfolio_id
          ) history ON TRUE
         WHERE portfolio.version IS DISTINCT FROM history.application_count
            OR portfolio.version IS DISTINCT FROM history.maximum_sequence
    ) THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_APPLICATION_CARDINALITY_MISMATCH'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_assert_paper_portfolio_application_cardinality()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    target_portfolio_id UUID;
    durable_version BIGINT;
    application_count BIGINT;
    maximum_sequence BIGINT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        target_portfolio_id := OLD.portfolio_id;
    ELSE
        target_portfolio_id := NEW.portfolio_id;
    END IF;

    SELECT portfolio.version,
           count(application.fill_id),
           coalesce(max(application.application_sequence), 0)
      INTO durable_version, application_count, maximum_sequence
      FROM paper_portfolios portfolio
      LEFT JOIN paper_portfolio_fill_applications application
        ON application.portfolio_id = portfolio.portfolio_id
     WHERE portfolio.portfolio_id = target_portfolio_id
     GROUP BY portfolio.version;

    IF NOT FOUND
       OR durable_version IS DISTINCT FROM application_count
       OR durable_version IS DISTINCT FROM maximum_sequence
    THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_APPLICATION_CARDINALITY_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_paper_portfolio_application_cardinality_portfolio
AFTER INSERT OR UPDATE ON paper_portfolios
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION hope_assert_paper_portfolio_application_cardinality();

CREATE CONSTRAINT TRIGGER trg_paper_portfolio_application_cardinality_history
AFTER INSERT OR UPDATE OR DELETE ON paper_portfolio_fill_applications
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION hope_assert_paper_portfolio_application_cardinality();
