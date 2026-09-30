-- Fail closed if impossible future-dated PAPER fill history predates the durable
-- per-write guard. Existing execution truth must never be silently normalized.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM fills f
        JOIN orders o ON o.order_id = f.order_id
        WHERE o.environment = 'PAPER'
          AND f.filled_at > clock_timestamp()
    ) THEN
        RAISE EXCEPTION 'PAPER_FILL_TIME_IN_FUTURE'
            USING ERRCODE = '23514';
    END IF;
END;
$$;
