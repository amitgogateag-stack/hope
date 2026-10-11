-- Authenticate immutable PAPER fills against their durable FILL effects.
-- Repository recovery already rejects a mismatched pair, but the database must
-- prevent that self-conflicting execution evidence from becoming durable.
CREATE OR REPLACE FUNCTION hope_paper_fill_payload_hash(
    fill_id UUID,
    order_id UUID,
    signal_id UUID,
    instrument_id UUID,
    side TEXT,
    quantity NUMERIC,
    fill_price NUMERIC,
    transaction_cost NUMERIC,
    slippage NUMERIC,
    cost_model_version TEXT,
    filled_at TIMESTAMPTZ
)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
STRICT
AS $$
    SELECT encode(
        digest(
            convert_to(
                fill_id::TEXT || '|' ||
                order_id::TEXT || '|' ||
                signal_id::TEXT || '|' ||
                instrument_id::TEXT || '|' ||
                side || '|' ||
                trim_scale(quantity)::TEXT || '|' ||
                trim_scale(fill_price)::TEXT || '|' ||
                trim_scale(transaction_cost)::TEXT || '|' ||
                trim_scale(slippage)::TEXT || '|' ||
                cost_model_version || '|' ||
                to_char(
                    filled_at AT TIME ZONE 'UTC',
                    'YYYY-MM-DD"T"HH24:MI:SS'
                ) ||
                CASE
                    WHEN (extract(microseconds FROM filled_at)::INTEGER %% 1000000) = 0
                    THEN ''
                    ELSE '.' || to_char(filled_at AT TIME ZONE 'UTC', 'US')
                END ||
                '+00:00',
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
          FROM fills fill
          JOIN orders source_order ON source_order.order_id = fill.order_id
          JOIN paper_effects effect
            ON effect.effect_type = 'FILL'
           AND effect.entity_id = fill.fill_id
         WHERE source_order.environment = 'PAPER'
           AND effect.payload_hash <> hope_paper_fill_payload_hash(
               fill.fill_id,
               fill.order_id,
               source_order.signal_id,
               source_order.instrument_id,
               source_order.side,
               fill.quantity,
               fill.fill_price,
               fill.transaction_cost,
               fill.slippage,
               fill.cost_model_version,
               fill.filled_at
           )
    ) THEN
        RAISE EXCEPTION 'PAPER_FILL_EFFECT_PAYLOAD_MISMATCH'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_lock_paper_fill_payload_identity(
    target_fill_id UUID
)
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended('hope:paper:fill-payload:' || target_fill_id, 0)
    );
END;
$$;

CREATE OR REPLACE FUNCTION hope_guard_paper_fill_effect_payload()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    source_order RECORD;
    durable_payload_hash TEXT;
BEGIN
    SELECT signal_id, instrument_id, environment, side
      INTO source_order
      FROM orders
     WHERE order_id = NEW.order_id;

    IF NOT FOUND OR source_order.environment <> 'PAPER' THEN
        RETURN NEW;
    END IF;

    PERFORM hope_lock_paper_fill_payload_identity(NEW.fill_id);

    SELECT payload_hash
      INTO durable_payload_hash
      FROM paper_effects
     WHERE effect_type = 'FILL'
       AND entity_id = NEW.fill_id;

    IF NOT FOUND THEN
        RETURN NEW;
    END IF;

    IF durable_payload_hash <> hope_paper_fill_payload_hash(
        NEW.fill_id,
        NEW.order_id,
        source_order.signal_id,
        source_order.instrument_id,
        source_order.side,
        NEW.quantity,
        NEW.fill_price,
        NEW.transaction_cost,
        NEW.slippage,
        NEW.cost_model_version,
        NEW.filled_at
    ) THEN
        RAISE EXCEPTION 'PAPER_FILL_EFFECT_PAYLOAD_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_zzzz_paper_fill_effect_payload
BEFORE INSERT ON fills
FOR EACH ROW
EXECUTE FUNCTION hope_guard_paper_fill_effect_payload();

CREATE OR REPLACE FUNCTION hope_guard_paper_effect_fill_payload()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    durable_fill RECORD;
BEGIN
    IF NEW.effect_type <> 'FILL' THEN
        RETURN NEW;
    END IF;

    PERFORM hope_lock_paper_fill_payload_identity(NEW.entity_id);

    SELECT fill.fill_id,
           fill.order_id,
           source_order.signal_id,
           source_order.instrument_id,
           source_order.environment,
           source_order.side,
           fill.quantity,
           fill.fill_price,
           fill.transaction_cost,
           fill.slippage,
           fill.cost_model_version,
           fill.filled_at
      INTO durable_fill
      FROM fills fill
      JOIN orders source_order ON source_order.order_id = fill.order_id
     WHERE fill.fill_id = NEW.entity_id;

    IF NOT FOUND OR durable_fill.environment <> 'PAPER' THEN
        RETURN NEW;
    END IF;

    IF NEW.payload_hash <> hope_paper_fill_payload_hash(
        durable_fill.fill_id,
        durable_fill.order_id,
        durable_fill.signal_id,
        durable_fill.instrument_id,
        durable_fill.side,
        durable_fill.quantity,
        durable_fill.fill_price,
        durable_fill.transaction_cost,
        durable_fill.slippage,
        durable_fill.cost_model_version,
        durable_fill.filled_at
    ) THEN
        RAISE EXCEPTION 'PAPER_FILL_EFFECT_PAYLOAD_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

-- Run after the existing claimed-job and portfolio serialization guards.
CREATE TRIGGER trg_zzzzzz_paper_effect_fill_payload
BEFORE INSERT ON paper_effects
FOR EACH ROW
EXECUTE FUNCTION hope_guard_paper_effect_fill_payload();
