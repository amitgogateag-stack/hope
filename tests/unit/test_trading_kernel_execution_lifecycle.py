from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from hope.application.trading.service import TradingKernel
from hope.domain.execution.models import Environment, OrderSide
from hope.domain.execution.simulator import CostModel, ExecutionQuote
from hope.domain.execution.timeline import ExecutionTimeline
from hope.domain.portfolio.ledger import PortfolioLedger
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.domain.signal.models import Signal, SignalType


UTC = timezone.utc
SIGNAL_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
INSTRUMENT_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
ORDER_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
FIRST_FILL_ID = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
SECOND_FILL_ID = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")


def test_trading_kernel_rejects_cumulative_overfill_without_mutating_portfolio():
    decision = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    signal = Signal(
        signal_id=SIGNAL_ID,
        instrument_id=INSTRUMENT_ID,
        strategy_version="test",
        decision_time=decision,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.5"),
        inputs_hash="e" * 64,
    )
    risk = RiskAssessment(
        signal_id=signal.signal_id,
        decision=RiskDecision.APPROVE,
        reason_code="TEST_APPROVED",
        approved_quantity=Decimal("10"),
    )
    timeline = ExecutionTimeline.from_decision(
        decision,
        latency=timedelta(0),
    )
    ledger = PortfolioLedger(Decimal("10000"))
    kernel = TradingKernel(ledger)

    submission = kernel.process(
        signal,
        risk,
        OrderSide.BUY,
        Environment.BACKTEST,
        None,
        None,
        order_id=ORDER_ID,
        timeline=timeline,
    )
    assert submission.intent is not None

    first_fill_time = decision + timedelta(minutes=1)
    first = kernel.execute_order(
        submission.intent,
        ORDER_ID,
        ExecutionQuote(
            INSTRUMENT_ID,
            first_fill_time,
            Decimal("100"),
            Decimal("100"),
        ),
        CostModel(version="test"),
        decision_time=decision,
        fill_id=FIRST_FILL_ID,
        timeline=timeline.with_fill_time(first_fill_time),
        quantity=Decimal("6"),
    )

    assert first.fill is not None
    assert first.fill.quantity == Decimal("6")
    assert ledger.state.positions[INSTRUMENT_ID].quantity == Decimal("6")

    second_fill_time = decision + timedelta(minutes=2)
    with pytest.raises(
        ValueError,
        match="FILL_EXCEEDS_REMAINING_ORDER_QUANTITY",
    ):
        kernel.execute_order(
            submission.intent,
            ORDER_ID,
            ExecutionQuote(
                INSTRUMENT_ID,
                second_fill_time,
                Decimal("101"),
                Decimal("101"),
            ),
            CostModel(version="test"),
            decision_time=decision,
            fill_id=SECOND_FILL_ID,
            timeline=timeline.with_fill_time(second_fill_time),
            quantity=Decimal("5"),
        )

    assert ledger.state.positions[INSTRUMENT_ID].quantity == Decimal("6")
    assert ledger.state.cash == Decimal("9400")
