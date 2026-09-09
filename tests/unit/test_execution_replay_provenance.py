from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.domain.execution import CostModel, Environment, ExecutionQuote, Order, OrderSide, simulate_market_fill
from hope.domain.execution.replay import ExecutionReplayError, replay_order
from hope.domain.execution.timeline import ExecutionTimeline


BASE_TIME = datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc)


def test_replay_requires_cost_model_provenance_before_mutation():
    order = Order(
        order_id=uuid4(),
        signal_id=uuid4(),
        instrument_id=uuid4(),
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        environment=Environment.PAPER,
    )
    quote = ExecutionQuote(order.instrument_id, BASE_TIME, Decimal("100"), Decimal("101"))
    timeline = ExecutionTimeline.from_decision(BASE_TIME, latency=timedelta(0))
    fill = simulate_market_fill(
        order,
        quote,
        uuid4(),
        CostModel("restart-test"),
        Decimal("4"),
        timeline=timeline,
    )
    malformed = replace(fill, cost_model_version="   ")

    with pytest.raises(ExecutionReplayError, match="FILL_COST_MODEL_VERSION_REQUIRED"):
        replay_order(order, [malformed], initial_cash=Decimal("10000"))
