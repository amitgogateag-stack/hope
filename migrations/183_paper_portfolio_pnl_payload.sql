-- Authenticate the durable PNL effect against the exact portfolio accounting
-- row before either can be accepted as recoverable evidence.
CREATE OR REPLACE FUNCTION hope_paper_portfolio_pnl_payload_hash(
    pnl_event_id UUID,
    portfolio_id UUID,
    fill_id UUID,
    instrument_id UUID,
    realized_pnl_delta NUMERIC,
    commission_delta NUMERIC,
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
                portfolio_id::TEXT || '|' ||
                fill_id::TEXT || '|' ||
                instrument_id::TEXT || '|' ||
                trim_scale(realized_pnl_delta)::TEXT || '|' ||
                trim_scale(commission_delta)::TEXT || '|' ||
                to_char(
                    event_time AT TIME ZONE 'UTC',
                    'YYYY-MM-DD"T"HH24:MI:SS'
                ) ||
                CASE
                    WHEN (extract(microseconds FROM event_time)::INTEGER % 1000000) = 0
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
          FROM paper_portfolio_pnl_events event
          JOIN paper_effects effect
            ON effect.effect_type = 'PNL'
           AND effect.entity_id = event.pnl_event_id
         WHERE effect.payload_hash <> hope_paper_portfolio_pnl_payload_hash(
             event.pnl_event_id,
             event.portfolio_id,
             event.fill_id,
             event.instrument_id,
             event.realized_pnl_delta,
             event.commission_delta,
             event.event_time
         )
    ) THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_PNL_EFFECT_PAYLOAD_MISMATCH'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_guard_paper_portfolio_pnl_effect_payload()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    durable_payload_hash TEXT;
    expected_payload_hash TEXT;
BEGIN
    SELECT payload_hash
      INTO durable_payload_hash
      FROM paper_effects
     WHERE effect_type = 'PNL'
       AND entity_id = NEW.pnl_event_id;

    -- Rows without a PNL effect are handled by the lineage guard whenever the
    -- source fill is production-tracked.  Legacy corruption probes remain
    -- available to exercise repository fail-closed recovery.
    IF NOT FOUND THEN
        RETURN NEW;
    END IF;

    expected_payload_hash := hope_paper_portfolio_pnl_payload_hash(
        NEW.pnl_event_id,
        NEW.portfolio_id,
        NEW.fill_id,
        NEW.instrument_id,
        NEW.realized_pnl_delta,
        NEW.commission_delta,
        NEW.event_time
    );

    IF durable_payload_hash <> expected_payload_hash THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_PNL_EFFECT_PAYLOAD_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_zzzz_paper_portfolio_pnl_effect_payload
BEFORE INSERT ON paper_portfolio_pnl_events
FOR EACH ROW
EXECUTE FUNCTION hope_guard_paper_portfolio_pnl_effect_payload();
