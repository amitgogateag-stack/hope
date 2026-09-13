from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.domain.execution import CostModel, Environment, ExecutionQuote, Order, OrderSide, simulate_market_fill
from hope.domain.execution.timeline import ExecutionTimeline


@pytest.mark.parametrize("quantity", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_simulator_rejects_non_finite_fill_quantity(quantity: Decimal) -> None:
    quote_time = datetime(2026, 1, 1, 14, 1, tzinfo=timezone.utc)
    instrument_id = uuid4()
    order = Order(
        order_id=uuid4(),
        signal_id=uuid4(),
        instrument_id=instrument_id,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        environment=Environment.PAPER,
    )
    quote = ExecutionQuote(instrument_id, quote_time, Decimal("99"), Decimal("100"))
    timeline = ExecutionTimeline.from_decision(
        quote_time - timedelta(minutes=1), latency=timedelta(0)
    )

    with pytest.raises(ValueError, match="FILL_QUANTITY_MUST_BE_FINITE"):
        simulate_market_fill(
            order,
            quote,
            uuid4(),
            CostModel("test"),
            quantity=quantity,
            timeline=timeline,
        )
