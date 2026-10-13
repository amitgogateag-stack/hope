-- Prevent canonical PAPER orders from persisting recovery lineage that the
-- portfolio recovery boundary must later reject.  Migration 006 binds the
-- instrument identity; canonical recovery also requires an actionable source
-- signal and the same signal type on both immutable records.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM orders source_order
          JOIN signals source_signal
            ON source_signal.signal_id = source_order.signal_id
         WHERE source_order.environment = 'PAPER'
           AND source_order.signal_type IS NOT NULL
           AND (
               source_signal.state <> 'SIGNAL'
               OR source_signal.signal_type IS DISTINCT FROM source_order.signal_type
           )
    ) THEN
        RAISE EXCEPTION 'PAPER_ORDER_SIGNAL_AUTHORIZATION_CONFLICT'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_guard_paper_order_signal_authorization()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    source_state TEXT;
    source_signal_type TEXT;
BEGIN
    -- Nullable signal_type identifies legacy rows whose recovery material was
    -- intentionally grandfathered by migration 168.  Canonical writers always
    -- provide it and must be fully bound to their immutable source signal.
    IF NEW.environment <> 'PAPER' OR NEW.signal_type IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT state, signal_type
      INTO source_state, source_signal_type
      FROM signals
     WHERE signal_id = NEW.signal_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'PAPER_ORDER_SOURCE_SIGNAL_MISSING'
            USING ERRCODE = '23514';
    END IF;
    IF source_state <> 'SIGNAL' THEN
        RAISE EXCEPTION 'PAPER_ORDER_SOURCE_SIGNAL_NOT_ACTIONABLE'
            USING ERRCODE = '23514';
    END IF;
    IF source_signal_type IS DISTINCT FROM NEW.signal_type THEN
        RAISE EXCEPTION 'PAPER_ORDER_SIGNAL_TYPE_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_order_signal_authorization
BEFORE INSERT ON orders
FOR EACH ROW
EXECUTE FUNCTION hope_guard_paper_order_signal_authorization();
