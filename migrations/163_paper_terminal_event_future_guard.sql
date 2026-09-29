-- Durable PAPER terminal truth must not claim a logical event from the future.
CREATE OR REPLACE FUNCTION hope_guard_paper_order_terminal_event()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    order_environment TEXT;
    order_quantity NUMERIC;
    source_decision_time TIMESTAMPTZ;
    filled NUMERIC;
    latest_fill_time TIMESTAMPTZ;
BEGIN
    SELECT source_order.environment, source_order.quantity, source_signal.decision_time
    INTO order_environment, order_quantity, source_decision_time
    FROM orders source_order
    JOIN signals source_signal ON source_signal.signal_id = source_order.signal_id
    WHERE source_order.order_id = NEW.order_id
    FOR UPDATE OF source_order;

    IF NOT FOUND OR order_environment <> 'PAPER' THEN
        RAISE EXCEPTION 'PAPER_TERMINAL_REQUIRES_PAPER_ORDER' USING ERRCODE = '23514';
    END IF;

    SELECT COALESCE(SUM(quantity), 0), MAX(filled_at)
    INTO filled, latest_fill_time
    FROM fills
    WHERE order_id = NEW.order_id;

    IF NEW.event_time > transaction_timestamp() THEN
        RAISE EXCEPTION 'PAPER_TERMINAL_EVENT_TIME_IN_FUTURE' USING ERRCODE = '23514';
    END IF;
    IF NEW.event_time < source_decision_time THEN
        RAISE EXCEPTION 'PAPER_TERMINAL_PRECEDES_DECISION_TIME' USING ERRCODE = '23514';
    END IF;
    IF latest_fill_time IS NOT NULL AND NEW.event_time < latest_fill_time THEN
        RAISE EXCEPTION 'PAPER_TERMINAL_PRECEDES_LATEST_FILL' USING ERRCODE = '23514';
    END IF;
    IF NEW.outcome = 'REJECTED' AND filled <> 0 THEN
        RAISE EXCEPTION 'PAPER_REJECTION_REQUIRES_UNFILLED_ORDER' USING ERRCODE = '23514';
    END IF;
    IF NEW.outcome = 'CANCELLED' AND NEW.cancelled_quantity <> order_quantity - filled THEN
        RAISE EXCEPTION 'PAPER_CANCELLATION_QUANTITY_MISMATCH' USING ERRCODE = '23514';
    END IF;
    IF filled >= order_quantity THEN
        RAISE EXCEPTION 'PAPER_TERMINAL_REQUIRES_REMAINING_QUANTITY' USING ERRCODE = '23514';
    END IF;
    IF NEW.created_at <> transaction_timestamp() THEN
        RAISE EXCEPTION 'PAPER_TERMINAL_TIMESTAMP_NOT_DATABASE_AUTHENTICATED' USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;
