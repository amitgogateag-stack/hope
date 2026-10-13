-- A canonical PAPER fill is execution truth, not staged intent. Before it
-- becomes durable, its source order must already be backed by the exact
-- durable APPROVE decision that authorized the order quantity.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM fills fill
          JOIN orders source_order ON source_order.order_id = fill.order_id
          LEFT JOIN paper_risk_assessments assessment
            ON assessment.signal_id = source_order.signal_id
         WHERE source_order.environment = 'PAPER'
           AND source_order.signal_type IS NOT NULL
           AND (
               assessment.signal_id IS NULL
               OR assessment.decision <> 'APPROVE'
               OR assessment.approved_quantity <> source_order.quantity
           )
    ) THEN
        RAISE EXCEPTION 'PAPER_FILL_RISK_AUTHORIZATION_CONFLICT'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_guard_paper_fill_risk_authorization()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    source_order RECORD;
    durable_risk RECORD;
BEGIN
    SELECT signal_id, environment, signal_type, quantity
      INTO source_order
      FROM orders
     WHERE order_id = NEW.order_id;

    IF NOT FOUND
       OR source_order.environment <> 'PAPER'
       OR source_order.signal_type IS NULL THEN
        RETURN NEW;
    END IF;

    -- Serialize with migration 210 in both order-first and risk-first flows.
    PERFORM hope_lock_paper_order_risk_authorization(source_order.signal_id);

    SELECT decision, approved_quantity
      INTO durable_risk
      FROM paper_risk_assessments
     WHERE signal_id = source_order.signal_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'PAPER_FILL_RISK_AUTHORIZATION_MISSING'
            USING ERRCODE = '23514';
    END IF;
    IF durable_risk.decision <> 'APPROVE' THEN
        RAISE EXCEPTION 'PAPER_FILL_RISK_NOT_APPROVED'
            USING ERRCODE = '23514';
    END IF;
    IF durable_risk.approved_quantity <> source_order.quantity THEN
        RAISE EXCEPTION 'PAPER_FILL_RISK_QUANTITY_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_fill_risk_authorization
BEFORE INSERT ON fills
FOR EACH ROW
EXECUTE FUNCTION hope_guard_paper_fill_risk_authorization();
