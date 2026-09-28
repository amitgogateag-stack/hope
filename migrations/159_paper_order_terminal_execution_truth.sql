-- Durable PAPER cancellation/rejection truth for restart-safe execution lifecycle reconstruction.
ALTER TABLE paper_effects DROP CONSTRAINT IF EXISTS paper_effects_effect_type_check;
ALTER TABLE paper_effects ADD CONSTRAINT paper_effects_effect_type_check CHECK (effect_type IN ('SIGNAL','RISK','ORDER','FILL','PNL','CANCELLATION','REJECTION'));
CREATE TABLE paper_order_terminal_events (
 order_id UUID PRIMARY KEY REFERENCES orders(order_id), outcome TEXT NOT NULL CHECK (outcome IN ('CANCELLED','REJECTED')),
 reason_code TEXT NOT NULL CHECK (reason_code <> '' AND reason_code=btrim(reason_code)), event_time TIMESTAMPTZ NOT NULL,
 cancelled_quantity NUMERIC, created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
 CONSTRAINT paper_order_terminal_cancel_quantity CHECK ((outcome='CANCELLED' AND cancelled_quantity IS NOT NULL AND cancelled_quantity>0) OR (outcome='REJECTED' AND cancelled_quantity IS NULL))
);
CREATE OR REPLACE FUNCTION hope_guard_paper_order_terminal_event() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE order_row orders%ROWTYPE; filled NUMERIC;
BEGIN
 SELECT * INTO order_row FROM orders WHERE order_id=NEW.order_id FOR UPDATE;
 IF NOT FOUND OR order_row.environment<>'PAPER' THEN RAISE EXCEPTION 'PAPER_TERMINAL_REQUIRES_PAPER_ORDER' USING ERRCODE='23514'; END IF;
 SELECT COALESCE(SUM(quantity),0) INTO filled FROM fills WHERE order_id=NEW.order_id;
 IF NEW.outcome='REJECTED' AND filled<>0 THEN RAISE EXCEPTION 'PAPER_REJECTION_REQUIRES_UNFILLED_ORDER' USING ERRCODE='23514'; END IF;
 IF NEW.outcome='CANCELLED' AND NEW.cancelled_quantity<>order_row.quantity-filled THEN RAISE EXCEPTION 'PAPER_CANCELLATION_QUANTITY_MISMATCH' USING ERRCODE='23514'; END IF;
 IF filled>=order_row.quantity THEN RAISE EXCEPTION 'PAPER_TERMINAL_REQUIRES_REMAINING_QUANTITY' USING ERRCODE='23514'; END IF;
 IF NEW.created_at<>transaction_timestamp() THEN RAISE EXCEPTION 'PAPER_TERMINAL_TIMESTAMP_NOT_DATABASE_AUTHENTICATED' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER trg_paper_order_terminal_guard BEFORE INSERT ON paper_order_terminal_events FOR EACH ROW EXECUTE FUNCTION hope_guard_paper_order_terminal_event();
CREATE OR REPLACE FUNCTION hope_reject_paper_order_terminal_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'PAPER_ORDER_TERMINAL_IMMUTABLE: terminal execution truth cannot be modified or deleted' USING ERRCODE='23514'; END; $$;
CREATE TRIGGER trg_paper_order_terminal_immutable BEFORE UPDATE OR DELETE ON paper_order_terminal_events FOR EACH ROW EXECUTE FUNCTION hope_reject_paper_order_terminal_mutation();
