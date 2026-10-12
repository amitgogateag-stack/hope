-- A PNL effect authenticates the event payload, but authentication alone does
-- not prove that the realized delta was derived from the immutable fill
-- sequence.  Recompute the average-cost transition at the deferred boundary so
-- a self-consistent but economically false event can never become durable.
CREATE OR REPLACE FUNCTION hope_expected_paper_portfolio_realized_pnl_delta(
    target_portfolio_id UUID,
    target_fill_id UUID
)
RETURNS NUMERIC LANGUAGE plpgsql AS $$
DECLARE
    target_application_sequence BIGINT;
    target_instrument_id UUID;
    source_fill RECORD;
    current_quantity NUMERIC := 0;
    average_price NUMERIC := 0;
    realized_pnl NUMERIC := 0;
    realized_before NUMERIC;
    signed_quantity NUMERIC;
    new_quantity NUMERIC;
    closing_quantity NUMERIC;
    total_absolute_quantity NUMERIC;
    expected_delta NUMERIC;
    target_seen BOOLEAN := FALSE;
BEGIN
    SELECT application.application_sequence, source_order.instrument_id
      INTO target_application_sequence, target_instrument_id
      FROM paper_portfolio_fill_applications application
      JOIN fills fill ON fill.fill_id = application.fill_id
      JOIN orders source_order ON source_order.order_id = fill.order_id
     WHERE application.portfolio_id = target_portfolio_id
       AND application.fill_id = target_fill_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_PNL_TRANSITION_PROJECTION_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    FOR source_fill IN
        SELECT application.fill_id,
               source_order.side,
               fill.quantity,
               fill.fill_price
          FROM paper_portfolio_fill_applications application
          JOIN fills fill ON fill.fill_id = application.fill_id
          JOIN orders source_order ON source_order.order_id = fill.order_id
         WHERE application.portfolio_id = target_portfolio_id
           AND source_order.instrument_id = target_instrument_id
           AND application.application_sequence <= target_application_sequence
         ORDER BY application.application_sequence
    LOOP
        realized_before := realized_pnl;
        signed_quantity := CASE source_fill.side
            WHEN 'BUY' THEN source_fill.quantity
            ELSE -source_fill.quantity
        END;
        new_quantity := current_quantity + signed_quantity;

        IF current_quantity = 0
           OR (current_quantity > 0 AND signed_quantity > 0)
           OR (current_quantity < 0 AND signed_quantity < 0) THEN
            total_absolute_quantity :=
                abs(current_quantity) + abs(signed_quantity);
            average_price := (
                abs(current_quantity) * average_price
                + abs(signed_quantity) * source_fill.fill_price
            ) / total_absolute_quantity;
        ELSE
            closing_quantity := least(
                abs(current_quantity), abs(signed_quantity)
            );
            IF current_quantity > 0 THEN
                realized_pnl := realized_pnl
                    + closing_quantity
                      * (source_fill.fill_price - average_price);
            ELSE
                realized_pnl := realized_pnl
                    + closing_quantity
                      * (average_price - source_fill.fill_price);
            END IF;

            IF new_quantity <> 0
               AND abs(signed_quantity) > abs(current_quantity) THEN
                average_price := source_fill.fill_price;
            ELSIF new_quantity = 0 THEN
                average_price := 0;
            END IF;
        END IF;

        current_quantity := new_quantity;
        IF source_fill.fill_id = target_fill_id THEN
            expected_delta := realized_pnl - realized_before;
            target_seen := TRUE;
        END IF;
    END LOOP;

    IF NOT target_seen THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_PNL_TRANSITION_PROJECTION_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    RETURN expected_delta;
END;
$$;

DO $$
DECLARE
    durable_event RECORD;
BEGIN
    FOR durable_event IN
        SELECT portfolio_id, fill_id, realized_pnl_delta
          FROM paper_portfolio_pnl_events
    LOOP
        IF durable_event.realized_pnl_delta IS DISTINCT FROM
           hope_expected_paper_portfolio_realized_pnl_delta(
               durable_event.portfolio_id,
               durable_event.fill_id
           ) THEN
            RAISE EXCEPTION 'PAPER_PORTFOLIO_PNL_TRANSITION_PROJECTION_MISMATCH'
                USING ERRCODE = '23514';
        END IF;
    END LOOP;
END;
$$;

CREATE OR REPLACE FUNCTION hope_assert_paper_portfolio_pnl_transition_projection()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.realized_pnl_delta IS DISTINCT FROM
       hope_expected_paper_portfolio_realized_pnl_delta(
           NEW.portfolio_id,
           NEW.fill_id
       ) THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_PNL_TRANSITION_PROJECTION_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_paper_portfolio_pnl_transition_projection
AFTER INSERT ON paper_portfolio_pnl_events
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION hope_assert_paper_portfolio_pnl_transition_projection();
