from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from hope.application.trading.service import TradingKernel
from hope.domain.execution.models import Environment, OrderSide
from hope.domain.execution.simulator import CostModel, ExecutionQuote, Fill
from hope.domain.execution.timeline import ExecutionTimeline
from hope.domain.portfolio.ledger import PortfolioLedger
from hope.domain.signal.models import SignalType
from hope.domain.trading.kernel import OrderIntent


UTC = timezone.utc
INSTRUMENT_ID = UUID("11111111-1111-1111-1111-111111111111")
ENTRY_SIGNAL_ID = UUID("22222222-2222-2222-2222-222222222222")
ENTRY_ORDER_ID = UUID("33333333-3333-3333-3333-333333333333")
ENTRY_FILL_ID = UUID("44444444-4444-4444-4444-444444444444")
EXIT_SIGNAL_ID = UUID("55555555-5555-5555-5555-555555555555")
EXIT_ORDER_ID = UUID("66666666-6666-6666-6666-666666666666")
EXIT_FILL_ID = UUID("77777777-7777-7777-7777-777777777777")


def test_fresh_kernel_exit_intent_cannot_reverse_existing_position():
    decision = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    ledger = PortfolioLedger(Decimal("10000"))
    ledger.apply_fill(
        Fill(
            fill_id=ENTRY_FILL_ID,
            order_id=ENTRY_ORDER_ID,
            signal_id=ENTRY_SIGNAL_ID,
            instrument_id=INSTRUMENT_ID,
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            price=Decimal("100"),
            commission=Decimal("0"),
            slippage=Decimal("0"),
            cost_model_version="seed",
            fill_time=decision,
        )
    )

    exit_intent = OrderIntent(
        signal_id=EXIT_SIGNAL_ID,
        instrument_id=INSTRUMENT_ID,
        side=OrderSide.SELL,
        quantity=Decimal("2"),
        environment=Environment.BACKTEST,
        signal_type=SignalType.EXIT,
    )
    fill_time = decision + timedelta(minutes=1)
    timeline = ExecutionTimeline.from_decision(
        decision,
        latency=timedelta(0),
    ).with_fill_time(fill_time)

    fresh_kernel = TradingKernel(ledger)
    with pytest.raises(ValueError, match="EXIT_QUANTITY_EXCEEDS_POSITION"):
        fresh_kernel.execute_order(
            exit_intent,
            EXIT_ORDER_ID,
            ExecutionQuote(
                INSTRUMENT_ID,
                fill_time,
                Decimal("101"),
                Decimal("101"),
            ),
            CostModel(version="test"),
            decision_time=decision,
            fill_id=EXIT_FILL_ID,
            timeline=timeline,
        )

    assert ledger.state.positions[INSTRUMENT_ID].quantity == Decimal("1")
    assert ledger.state.cash == Decimal("9900")
