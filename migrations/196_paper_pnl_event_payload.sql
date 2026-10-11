-- Authenticate legacy position-scoped P&L rows against their durable effects.
-- Repository validation already rejects mismatches, but direct or competing
-- writers must not be able to commit a self-conflicting event/effect pair that
-- recovery can discover only after the transaction is durable.
CREATE OR REPLACE FUNCTION hope_paper_pnl_payload_hash(
    pnl_event_id UUID,
    position_id UUID,
    amount NUMERIC,
    event_time TIMESTAMPTZ
)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
STRICT
AS $$
    SELECT encode(
        digest(
            convert_to(
                pnl_event_id::TEXT || '|' ||
                position_id::TEXT || '|' ||
                trim_scale(amount)::TEXT || '|' ||
                to_char(
                    event_time AT TIME ZONE 'UTC',
                    'YYYY-MM-DD"T"HH24:MI:SS'
                ) ||
                CASE
                    WHEN (extract(microseconds FROM event_time)::INTEGER %% 1000000) = 0
                    THEN ''
                    ELSE '.' || to_char(event_time AT TIME ZONE 'UTC', 'US')
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
          FROM pnl_events event
          JOIN paper_effects effect
            ON effect.effect_type = 'PNL'
           AND effect.entity_id = event.pnl_event_id
         WHERE effect.payload_hash <> hope_paper_pnl_payload_hash(
             event.pnl_event_id,
             event.position_id,
             event.amount,
             event.event_time
         )
    ) THEN
        RAISE EXCEPTION 'PAPER_PNL_EFFECT_PAYLOAD_MISMATCH'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_guard_paper_pnl_event_effect_payload()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    durable_payload_hash TEXT;
BEGIN
    SELECT payload_hash
      INTO durable_payload_hash
      FROM paper_effects
     WHERE effect_type = 'PNL'
       AND entity_id = NEW.pnl_event_id;

    -- The reciprocal effect trigger validates event-first writes. If the effect
    -- is not present yet, deferred completeness migration 195 requires it before
    -- commit and this trigger will validate the effect-first order then.
    IF NOT FOUND THEN
        RETURN NEW;
    END IF;

    IF durable_payload_hash <> hope_paper_pnl_payload_hash(
        NEW.pnl_event_id,
        NEW.position_id,
        NEW.amount,
        NEW.event_time
    ) THEN
        RAISE EXCEPTION 'PAPER_PNL_EFFECT_PAYLOAD_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_pnl_event_effect_payload
BEFORE INSERT ON pnl_events
FOR EACH ROW
EXECUTE FUNCTION hope_guard_paper_pnl_event_effect_payload();

CREATE OR REPLACE FUNCTION hope_guard_paper_pnl_effect_event_payload()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    expected_payload_hash TEXT;
BEGIN
    IF NEW.effect_type <> 'PNL' THEN
        RETURN NEW;
    END IF;

    SELECT hope_paper_pnl_payload_hash(
               event.pnl_event_id,
               event.position_id,
               event.amount,
               event.event_time
           )
      INTO expected_payload_hash
      FROM pnl_events event
     WHERE event.pnl_event_id = NEW.entity_id;

    IF NOT FOUND THEN
        RETURN NEW;
    END IF;

    IF NEW.payload_hash <> expected_payload_hash THEN
        RAISE EXCEPTION 'PAPER_PNL_EFFECT_PAYLOAD_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_zzzzz_paper_effect_pnl_event_payload
BEFORE INSERT ON paper_effects
FOR EACH ROW
EXECUTE FUNCTION hope_guard_paper_pnl_effect_event_payload();
