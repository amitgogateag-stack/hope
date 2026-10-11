-- Materialized PAPER cash is a deterministic projection of immutable applied
-- fills.  Recovery already detects drift, but a deferred database invariant
-- prevents that drift from committing while preserving the repository's safe
-- portfolio-first, application-second statement order.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM paper_portfolios portfolio
          LEFT JOIN LATERAL (
              SELECT coalesce(
                         sum(
                             CASE source_order.side
                                 WHEN 'BUY' THEN
                                     -(fill.fill_price * fill.quantity)
                                 ELSE fill.fill_price * fill.quantity
                             END - fill.transaction_cost
                         ),
                         0
                     ) AS cash_delta
                FROM paper_portfolio_fill_applications application
                JOIN fills fill ON fill.fill_id = application.fill_id
                JOIN orders source_order ON source_order.order_id = fill.order_id
               WHERE application.portfolio_id = portfolio.portfolio_id
          ) history ON TRUE
         WHERE portfolio.cash IS DISTINCT FROM
               portfolio.initial_cash + history.cash_delta
    ) THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_CASH_PROJECTION_MISMATCH'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_assert_paper_portfolio_cash_projection()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    target_portfolio_id UUID;
    durable_cash NUMERIC;
    expected_cash NUMERIC;
BEGIN
    target_portfolio_id := NEW.portfolio_id;

    SELECT portfolio.cash,
           portfolio.initial_cash + coalesce(
               sum(
                   CASE source_order.side
                       WHEN 'BUY' THEN -(fill.fill_price * fill.quantity)
                       ELSE fill.fill_price * fill.quantity
                   END - fill.transaction_cost
               ),
               0
           )
      INTO durable_cash, expected_cash
      FROM paper_portfolios portfolio
      LEFT JOIN paper_portfolio_fill_applications application
        ON application.portfolio_id = portfolio.portfolio_id
      LEFT JOIN fills fill ON fill.fill_id = application.fill_id
      LEFT JOIN orders source_order ON source_order.order_id = fill.order_id
     WHERE portfolio.portfolio_id = target_portfolio_id
     GROUP BY portfolio.cash, portfolio.initial_cash;

    IF NOT FOUND OR durable_cash IS DISTINCT FROM expected_cash THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_CASH_PROJECTION_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_paper_portfolio_cash_projection_portfolio
AFTER INSERT OR UPDATE ON paper_portfolios
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION hope_assert_paper_portfolio_cash_projection();

CREATE CONSTRAINT TRIGGER trg_paper_portfolio_cash_projection_application
AFTER INSERT ON paper_portfolio_fill_applications
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION hope_assert_paper_portfolio_cash_projection();
