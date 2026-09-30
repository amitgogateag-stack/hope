-- A future-dated PAPER fill may have been written before the durable guard existed.
-- Refuse migration/startup validation if durable execution history already contains
-- such impossible future truth; historical execution evidence must be repaired by
-- an explicit audited recovery path rather than silently accepted.
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
