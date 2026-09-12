-- Authoritative PAPER P&L commission must equal the source fill transaction cost.
CREATE OR REPLACE FUNCTION hope_guard_paper_portfolio_pnl_commission()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    source_transaction_cost NUMERIC;
BEGIN
    SELECT transaction_cost INTO source_transaction_cost
    FROM fills
    WHERE fill_id = NEW.fill_id;

    IF source_transaction_cost IS DISTINCT FROM NEW.commission_delta THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = concat(
                'PAPER_PORTFOLIO_PNL_COMMISSION_MISMATCH: fill ', NEW.fill_id,
                ' transaction_cost ', source_transaction_cost,
                ' vs pnl commission_delta ', NEW.commission_delta
            );
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_portfolio_pnl_commission_integrity
BEFORE INSERT ON paper_portfolio_pnl_events
FOR EACH ROW
EXECUTE FUNCTION hope_guard_paper_portfolio_pnl_commission();
