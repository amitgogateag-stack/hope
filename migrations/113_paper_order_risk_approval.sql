-- Every durable PAPER order must be backed by an approving durable risk
-- assessment for the same signal and exact quantity. This closes the storage
-- boundary so direct repository/SQL paths cannot bypass runtime risk approval.

CREATE OR REPLACE FUNCTION hope_require_paper_order_risk_approval()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    risk_decision TEXT;
    risk_quantity NUMERIC;
BEGIN
    SELECT decision, approved_quantity
    INTO risk_decision, risk_quantity
    FROM paper_risk_assessments
    WHERE signal_id = NEW.signal_id;

    IF risk_decision IS DISTINCT FROM 'APPROVE' THEN
        RAISE EXCEPTION 'PAPER_ORDER_REQUIRES_APPROVING_RISK_ASSESSMENT: signal %', NEW.signal_id
            USING ERRCODE = '23514';
    END IF;

    IF risk_quantity IS DISTINCT FROM NEW.quantity THEN
        RAISE EXCEPTION 'PAPER_ORDER_RISK_QUANTITY_MISMATCH: signal % approved % vs order %',
            NEW.signal_id, risk_quantity, NEW.quantity
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_paper_order_requires_risk_approval ON orders;
CREATE TRIGGER trg_paper_order_requires_risk_approval
BEFORE INSERT ON orders
FOR EACH ROW
WHEN (NEW.environment = 'PAPER')
EXECUTE FUNCTION hope_require_paper_order_risk_approval();
