-- Persisted orders are immutable intent records. Lifecycle outcomes are represented by execution/fill history, not by rewriting the submitted order.

CREATE OR REPLACE FUNCTION hope_reject_order_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'ORDER_IMMUTABLE: order history cannot be modified or deleted'
        USING ERRCODE = '23514';
END;
$$;

DROP TRIGGER IF EXISTS trg_orders_immutable ON orders;
CREATE TRIGGER trg_orders_immutable
BEFORE UPDATE OR DELETE ON orders
FOR EACH ROW EXECUTE FUNCTION hope_reject_order_mutation();
