-- Every durable PAPER position is a deterministic average-cost projection of
-- immutable applied fills.  Recovery verifies this projection when it reads,
-- while these deferred checks prevent drift from becoming durable at commit.
CREATE OR REPLACE FUNCTION hope_validate_paper_portfolio_position_projection(
    target_portfolio_id UUID
)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    target_instrument_id UUID;
    source_fill RECORD;
    expected_quantity NUMERIC;
    expected_average_price NUMERIC;
    expected_realized_pnl NUMERIC;
    expected_total_commission NUMERIC;
    signed_quantity NUMERIC;
    new_quantity NUMERIC;
    closing_quantity NUMERIC;
    total_absolute_quantity NUMERIC;
    durable_quantity NUMERIC;
    durable_average_price NUMERIC;
    durable_realized_pnl NUMERIC;
    durable_total_commission NUMERIC;
BEGIN
    FOR target_instrument_id IN
        SELECT position.instrument_id
          FROM paper_portfolio_positions position
         WHERE position.portfolio_id = target_portfolio_id
        UNION
        SELECT source_order.instrument_id
          FROM paper_portfolio_fill_applications application
          JOIN fills fill ON fill.fill_id = application.fill_id
          JOIN orders source_order ON source_order.order_id = fill.order_id
         WHERE application.portfolio_id = target_portfolio_id
    LOOP
        expected_quantity := 0;
        expected_average_price := 0;
        expected_realized_pnl := 0;
        expected_total_commission := 0;

        FOR source_fill IN
            SELECT source_order.side,
                   fill.quantity,
                   fill.fill_price,
                   fill.transaction_cost
              FROM paper_portfolio_fill_applications application
              JOIN fills fill ON fill.fill_id = application.fill_id
              JOIN orders source_order ON source_order.order_id = fill.order_id
             WHERE application.portfolio_id = target_portfolio_id
               AND source_order.instrument_id = target_instrument_id
             ORDER BY application.application_sequence
        LOOP
            signed_quantity := CASE source_fill.side
                WHEN 'BUY' THEN source_fill.quantity
                ELSE -source_fill.quantity
            END;
            new_quantity := expected_quantity + signed_quantity;

            IF expected_quantity = 0
               OR (expected_quantity > 0 AND signed_quantity > 0)
               OR (expected_quantity < 0 AND signed_quantity < 0) THEN
                total_absolute_quantity :=
                    abs(expected_quantity) + abs(signed_quantity);
                expected_average_price := (
                    abs(expected_quantity) * expected_average_price
                    + abs(signed_quantity) * source_fill.fill_price
                ) / total_absolute_quantity;
            ELSE
                closing_quantity := least(
                    abs(expected_quantity), abs(signed_quantity)
                );
                IF expected_quantity > 0 THEN
                    expected_realized_pnl := expected_realized_pnl
                        + closing_quantity
                          * (source_fill.fill_price - expected_average_price);
                ELSE
                    expected_realized_pnl := expected_realized_pnl
                        + closing_quantity
                          * (expected_average_price - source_fill.fill_price);
                END IF;

                IF new_quantity <> 0
                   AND abs(signed_quantity) > abs(expected_quantity) THEN
                    expected_average_price := source_fill.fill_price;
                ELSIF new_quantity = 0 THEN
                    expected_average_price := 0;
                END IF;
            END IF;

            expected_quantity := new_quantity;
            expected_total_commission := expected_total_commission
                + source_fill.transaction_cost;
        END LOOP;

        SELECT position.quantity,
               position.average_price,
               position.realized_pnl,
               position.total_commission
          INTO durable_quantity,
               durable_average_price,
               durable_realized_pnl,
               durable_total_commission
          FROM paper_portfolio_positions position
         WHERE position.portfolio_id = target_portfolio_id
           AND position.instrument_id = target_instrument_id;

        IF NOT FOUND
           OR durable_quantity IS DISTINCT FROM expected_quantity
           OR durable_average_price IS DISTINCT FROM expected_average_price
           OR durable_realized_pnl IS DISTINCT FROM expected_realized_pnl
           OR durable_total_commission IS DISTINCT FROM expected_total_commission THEN
            RAISE EXCEPTION 'PAPER_PORTFOLIO_POSITION_PROJECTION_MISMATCH'
                USING ERRCODE = '23514';
        END IF;
    END LOOP;
END;
$$;

DO $$
DECLARE
    target_portfolio_id UUID;
BEGIN
    FOR target_portfolio_id IN
        SELECT portfolio_id FROM paper_portfolios
    LOOP
        PERFORM hope_validate_paper_portfolio_position_projection(
            target_portfolio_id
        );
    END LOOP;
END;
$$;

CREATE OR REPLACE FUNCTION hope_assert_paper_portfolio_position_projection()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM hope_validate_paper_portfolio_position_projection(NEW.portfolio_id);
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_paper_portfolio_position_projection_position
AFTER INSERT OR UPDATE ON paper_portfolio_positions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION hope_assert_paper_portfolio_position_projection();

CREATE CONSTRAINT TRIGGER trg_paper_portfolio_position_projection_application
AFTER INSERT ON paper_portfolio_fill_applications
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION hope_assert_paper_portfolio_position_projection();
