-- Bind canonical PAPER orders to their durable risk authorization whenever
-- both records exist. Completeness remains staged: risk may be committed before
-- the order, and legacy/order-only fixtures remain valid until accounting uses
-- them. Once paired, however, recovery must never observe contradictory facts.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM orders source_order
          JOIN paper_risk_assessments assessment
            ON assessment.signal_id = source_order.signal_id
         WHERE source_order.environment = 'PAPER'
           AND source_order.signal_type IS NOT NULL
           AND (
               assessment.decision <> 'APPROVE'
               OR assessment.approved_quantity <> source_order.quantity
           )
    ) THEN
        RAISE EXCEPTION 'PAPER_ORDER_RISK_AUTHORIZATION_CONFLICT'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_lock_paper_order_risk_authorization(
    target_signal_id UUID
)
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            'hope:paper:order-risk-authorization:' || target_signal_id,
            0
        )
    );
END;
$$;

CREATE OR REPLACE FUNCTION hope_assert_paper_order_risk_authorization(
    target_signal_id UUID,
    target_quantity NUMERIC,
    target_decision TEXT,
    target_approved_quantity NUMERIC
)
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    IF target_decision <> 'APPROVE' THEN
        RAISE EXCEPTION 'PAPER_ORDER_RISK_NOT_APPROVED'
            USING ERRCODE = '23514';
    END IF;
    IF target_approved_quantity <> target_quantity THEN
        RAISE EXCEPTION 'PAPER_ORDER_RISK_QUANTITY_MISMATCH'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_guard_paper_order_risk_authorization()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    durable_risk RECORD;
BEGIN
    IF NEW.environment <> 'PAPER' OR NEW.signal_type IS NULL THEN
        RETURN NEW;
    END IF;

    PERFORM hope_lock_paper_order_risk_authorization(NEW.signal_id);

    SELECT decision, approved_quantity
      INTO durable_risk
      FROM paper_risk_assessments
     WHERE signal_id = NEW.signal_id;

    IF FOUND THEN
        PERFORM hope_assert_paper_order_risk_authorization(
            NEW.signal_id,
            NEW.quantity,
            durable_risk.decision,
            durable_risk.approved_quantity
        );
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_order_risk_authorization
BEFORE INSERT ON orders
FOR EACH ROW
EXECUTE FUNCTION hope_guard_paper_order_risk_authorization();

CREATE OR REPLACE FUNCTION hope_guard_paper_risk_order_authorization()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    durable_order RECORD;
BEGIN
    PERFORM hope_lock_paper_order_risk_authorization(NEW.signal_id);

    FOR durable_order IN
        SELECT quantity
          FROM orders
         WHERE signal_id = NEW.signal_id
           AND environment = 'PAPER'
           AND signal_type IS NOT NULL
    LOOP
        PERFORM hope_assert_paper_order_risk_authorization(
            NEW.signal_id,
            durable_order.quantity,
            NEW.decision,
            NEW.approved_quantity
        );
    END LOOP;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_risk_order_authorization
BEFORE INSERT ON paper_risk_assessments
FOR EACH ROW
EXECUTE FUNCTION hope_guard_paper_risk_order_authorization();
