-- Bind each durable PAPER order to the exact ORDER effect payload used by
-- restart recovery.  Completeness remains a separate concern: this migration
-- authenticates a pair whenever both sides exist without retroactively making
-- every legacy/non-PAPER order part of the PAPER effect topology.
CREATE OR REPLACE FUNCTION hope_paper_order_payload_hash(
    order_id UUID,
    signal_id UUID,
    instrument_id UUID,
    side TEXT,
    quantity NUMERIC,
    environment TEXT,
    signal_type TEXT
)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
STRICT
AS $$
    SELECT encode(
        digest(
            convert_to(
                order_id::TEXT || '|' ||
                signal_id::TEXT || '|' ||
                instrument_id::TEXT || '|' ||
                side || '|' ||
                trim_scale(quantity)::TEXT || '|' ||
                environment || '|' ||
                signal_type,
                'UTF8'
            ),
            'sha256'
        ),
        'hex'
    );
$$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM orders source_order
          JOIN paper_effects effect
            ON effect.effect_type = 'ORDER'
           AND effect.entity_id = source_order.order_id
         WHERE source_order.environment <> 'PAPER'
    ) THEN
        RAISE EXCEPTION 'PAPER_ORDER_EFFECT_REQUIRES_PAPER_ORDER'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM orders source_order
          JOIN paper_effects effect
            ON effect.effect_type = 'ORDER'
           AND effect.entity_id = source_order.order_id
         WHERE source_order.environment = 'PAPER'
           AND source_order.signal_type IS NULL
    ) THEN
        RAISE EXCEPTION 'PAPER_ORDER_RECOVERY_MATERIAL_MISSING'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM orders source_order
          JOIN paper_effects effect
            ON effect.effect_type = 'ORDER'
           AND effect.entity_id = source_order.order_id
         WHERE source_order.environment = 'PAPER'
           AND effect.payload_hash <> hope_paper_order_payload_hash(
               source_order.order_id,
               source_order.signal_id,
               source_order.instrument_id,
               source_order.side,
               source_order.quantity,
               source_order.environment,
               source_order.signal_type
           )
    ) THEN
        RAISE EXCEPTION 'PAPER_ORDER_EFFECT_PAYLOAD_MISMATCH'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_lock_paper_order_payload_identity(
    target_order_id UUID
)
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended('hope:paper:order-payload:' || target_order_id, 0)
    );
END;
$$;

CREATE OR REPLACE FUNCTION hope_guard_paper_order_payload()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    durable_payload_hash TEXT;
BEGIN
    PERFORM hope_lock_paper_order_payload_identity(NEW.order_id);

    SELECT payload_hash
      INTO durable_payload_hash
      FROM paper_effects
     WHERE effect_type = 'ORDER'
       AND entity_id = NEW.order_id;

    IF NOT FOUND THEN
        RETURN NEW;
    END IF;

    IF NEW.environment <> 'PAPER' THEN
        RAISE EXCEPTION 'PAPER_ORDER_EFFECT_REQUIRES_PAPER_ORDER'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.signal_type IS NULL THEN
        RAISE EXCEPTION 'PAPER_ORDER_RECOVERY_MATERIAL_MISSING'
            USING ERRCODE = '23514';
    END IF;

    IF durable_payload_hash <> hope_paper_order_payload_hash(
        NEW.order_id,
        NEW.signal_id,
        NEW.instrument_id,
        NEW.side,
        NEW.quantity,
        NEW.environment,
        NEW.signal_type
    ) THEN
        RAISE EXCEPTION 'PAPER_ORDER_EFFECT_PAYLOAD_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_zzzz_paper_order_payload
BEFORE INSERT ON orders
FOR EACH ROW
EXECUTE FUNCTION hope_guard_paper_order_payload();

CREATE OR REPLACE FUNCTION hope_guard_paper_order_effect_payload()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    durable_order RECORD;
BEGIN
    IF NEW.effect_type <> 'ORDER' THEN
        RETURN NEW;
    END IF;

    PERFORM hope_lock_paper_order_payload_identity(NEW.entity_id);

    SELECT order_id, signal_id, instrument_id, side, quantity,
           environment, signal_type
      INTO durable_order
      FROM orders
     WHERE order_id = NEW.entity_id;

    IF NOT FOUND THEN
        RETURN NEW;
    END IF;

    IF durable_order.environment <> 'PAPER' THEN
        RAISE EXCEPTION 'PAPER_ORDER_EFFECT_REQUIRES_PAPER_ORDER'
            USING ERRCODE = '23514';
    END IF;

    IF durable_order.signal_type IS NULL THEN
        RAISE EXCEPTION 'PAPER_ORDER_RECOVERY_MATERIAL_MISSING'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.payload_hash <> hope_paper_order_payload_hash(
        durable_order.order_id,
        durable_order.signal_id,
        durable_order.instrument_id,
        durable_order.side,
        durable_order.quantity,
        durable_order.environment,
        durable_order.signal_type
    ) THEN
        RAISE EXCEPTION 'PAPER_ORDER_EFFECT_PAYLOAD_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_zzzzzzzz_paper_order_effect_payload
BEFORE INSERT ON paper_effects
FOR EACH ROW
EXECUTE FUNCTION hope_guard_paper_order_effect_payload();
