-- Bind immutable PAPER cancellation/rejection rows to their exact durable
-- effects. Canonicalize event instants to UTC so restart verification is
-- independent of the PostgreSQL session timezone.
CREATE OR REPLACE FUNCTION hope_paper_terminal_payload_hash(
    order_id UUID,
    signal_id UUID,
    instrument_id UUID,
    outcome TEXT,
    reason_code TEXT,
    event_time TIMESTAMPTZ,
    cancelled_quantity NUMERIC
)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT encode(
        digest(
            convert_to(
                order_id::TEXT || '|' ||
                signal_id::TEXT || '|' ||
                instrument_id::TEXT || '|' ||
                outcome || '|' ||
                reason_code || '|' ||
                to_char(
                    event_time AT TIME ZONE 'UTC',
                    'YYYY-MM-DD"T"HH24:MI:SS'
                ) ||
                CASE
                    WHEN (extract(microseconds FROM event_time)::INTEGER %% 1000000) = 0
                    THEN ''
                    ELSE '.' || to_char(event_time AT TIME ZONE 'UTC', 'US')
                END ||
                '+00:00' ||
                CASE
                    WHEN outcome = 'CANCELLED'
                    THEN '|' || trim_scale(cancelled_quantity)::TEXT
                    ELSE ''
                END,
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
          FROM paper_order_terminal_events terminal
          JOIN orders source_order ON source_order.order_id = terminal.order_id
          LEFT JOIN paper_effects effect
            ON effect.entity_id = terminal.order_id
           AND effect.effect_type = CASE terminal.outcome
               WHEN 'CANCELLED' THEN 'CANCELLATION'
               ELSE 'REJECTION'
           END
         WHERE effect.effect_id IS NULL
            OR effect.payload_hash <> hope_paper_terminal_payload_hash(
                terminal.order_id,
                source_order.signal_id,
                source_order.instrument_id,
                terminal.outcome,
                terminal.reason_code,
                terminal.event_time,
                terminal.cancelled_quantity
            )
    ) THEN
        RAISE EXCEPTION 'PAPER_TERMINAL_EFFECT_MISSING_OR_PAYLOAD_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM paper_effects effect
          LEFT JOIN paper_order_terminal_events terminal
            ON terminal.order_id = effect.entity_id
           AND effect.effect_type = CASE terminal.outcome
               WHEN 'CANCELLED' THEN 'CANCELLATION'
               ELSE 'REJECTION'
           END
         WHERE effect.effect_type IN ('CANCELLATION', 'REJECTION')
           AND terminal.order_id IS NULL
    ) THEN
        RAISE EXCEPTION 'PAPER_TERMINAL_EFFECT_WITHOUT_EVENT'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_lock_paper_terminal_payload_identity(
    target_order_id UUID
)
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended('hope:paper:terminal-payload:' || target_order_id, 0)
    );
END;
$$;

CREATE OR REPLACE FUNCTION hope_guard_paper_terminal_event_payload()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    source_order RECORD;
    durable_effect RECORD;
    expected_effect_type TEXT;
BEGIN
    PERFORM hope_lock_paper_terminal_payload_identity(NEW.order_id);

    SELECT signal_id, instrument_id, environment
      INTO source_order
      FROM orders
     WHERE order_id = NEW.order_id;
    IF NOT FOUND OR source_order.environment <> 'PAPER' THEN
        RETURN NEW;
    END IF;

    expected_effect_type := CASE NEW.outcome
        WHEN 'CANCELLED' THEN 'CANCELLATION'
        ELSE 'REJECTION'
    END;
    SELECT effect_type, payload_hash
      INTO durable_effect
      FROM paper_effects
     WHERE entity_id = NEW.order_id
       AND effect_type IN ('CANCELLATION', 'REJECTION');

    IF NOT FOUND THEN
        RETURN NEW;
    END IF;
    IF durable_effect.effect_type <> expected_effect_type
       OR durable_effect.payload_hash <> hope_paper_terminal_payload_hash(
           NEW.order_id,
           source_order.signal_id,
           source_order.instrument_id,
           NEW.outcome,
           NEW.reason_code,
           NEW.event_time,
           NEW.cancelled_quantity
       )
    THEN
        RAISE EXCEPTION 'PAPER_TERMINAL_EFFECT_PAYLOAD_MISMATCH'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_zzzz_paper_terminal_event_payload
BEFORE INSERT ON paper_order_terminal_events
FOR EACH ROW
EXECUTE FUNCTION hope_guard_paper_terminal_event_payload();

CREATE OR REPLACE FUNCTION hope_guard_paper_terminal_effect_payload()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    durable_terminal RECORD;
    expected_effect_type TEXT;
BEGIN
    IF NEW.effect_type NOT IN ('CANCELLATION', 'REJECTION') THEN
        RETURN NEW;
    END IF;
    PERFORM hope_lock_paper_terminal_payload_identity(NEW.entity_id);

    SELECT terminal.order_id,
           terminal.outcome,
           terminal.reason_code,
           terminal.event_time,
           terminal.cancelled_quantity,
           source_order.signal_id,
           source_order.instrument_id
      INTO durable_terminal
      FROM paper_order_terminal_events terminal
      JOIN orders source_order ON source_order.order_id = terminal.order_id
     WHERE terminal.order_id = NEW.entity_id;
    IF NOT FOUND THEN
        RETURN NEW;
    END IF;

    expected_effect_type := CASE durable_terminal.outcome
        WHEN 'CANCELLED' THEN 'CANCELLATION'
        ELSE 'REJECTION'
    END;
    IF NEW.effect_type <> expected_effect_type
       OR NEW.payload_hash <> hope_paper_terminal_payload_hash(
           durable_terminal.order_id,
           durable_terminal.signal_id,
           durable_terminal.instrument_id,
           durable_terminal.outcome,
           durable_terminal.reason_code,
           durable_terminal.event_time,
           durable_terminal.cancelled_quantity
       )
    THEN
        RAISE EXCEPTION 'PAPER_TERMINAL_EFFECT_PAYLOAD_MISMATCH'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_zzzzzzz_paper_terminal_effect_payload
BEFORE INSERT ON paper_effects
FOR EACH ROW
EXECUTE FUNCTION hope_guard_paper_terminal_effect_payload();

CREATE OR REPLACE FUNCTION hope_require_paper_terminal_event_effect()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    expected_effect_type TEXT;
BEGIN
    expected_effect_type := CASE NEW.outcome
        WHEN 'CANCELLED' THEN 'CANCELLATION'
        ELSE 'REJECTION'
    END;
    IF NOT EXISTS (
        SELECT 1 FROM paper_effects
         WHERE effect_type = expected_effect_type
           AND entity_id = NEW.order_id
    ) THEN
        RAISE EXCEPTION 'PAPER_TERMINAL_EVENT_WITHOUT_EFFECT'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_paper_terminal_event_effect_completeness
AFTER INSERT ON paper_order_terminal_events
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION hope_require_paper_terminal_event_effect();

CREATE OR REPLACE FUNCTION hope_require_paper_terminal_effect_event()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.effect_type NOT IN ('CANCELLATION', 'REJECTION') THEN
        RETURN NULL;
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM paper_order_terminal_events terminal
         WHERE terminal.order_id = NEW.entity_id
           AND NEW.effect_type = CASE terminal.outcome
               WHEN 'CANCELLED' THEN 'CANCELLATION'
               ELSE 'REJECTION'
           END
    ) THEN
        RAISE EXCEPTION 'PAPER_TERMINAL_EFFECT_WITHOUT_EVENT'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_paper_terminal_effect_event_completeness
AFTER INSERT ON paper_effects
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION hope_require_paper_terminal_effect_event();
