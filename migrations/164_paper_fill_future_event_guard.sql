-- Durable PAPER fill truth must not claim a logical execution event from the future.
CREATE OR REPLACE FUNCTION hope_enforce_paper_fill_decision_time()
RETURNS trigger AS $$
DECLARE
    source_environment TEXT;
    source_decision_time TIMESTAMPTZ;
BEGIN
    SELECT o.environment, s.decision_time
    INTO source_environment, source_decision_time
    FROM orders o
    JOIN signals s ON s.signal_id = o.signal_id
    WHERE o.order_id = NEW.order_id;

    IF source_environment = 'PAPER' AND NEW.filled_at > transaction_timestamp() THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'PAPER_FILL_TIME_IN_FUTURE';
    END IF;

    IF source_environment = 'PAPER' AND NEW.filled_at < source_decision_time THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'PAPER_FILL_PRECEDES_DECISION_TIME';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
