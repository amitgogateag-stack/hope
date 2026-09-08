from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from hope.application.backtests.engine import DeterministicBacktest
from hope.domain.execution.models import OrderSide
from hope.domain.execution.simulator import CostModel
from hope.domain.market_data.models import MarketBar
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.domain.signal.models import Signal, SignalType


UTC = timezone.utc
INSTRUMENT = "11111111-1111-1111-1111-111111111111"


def make_bar(event_time: datetime, available_time: datetime, close: str) -> MarketBar:
    price = Decimal(close)
    return MarketBar(
        instrument_id=INSTRUMENT,
        event_time=event_time,
        available_time=available_time,
        ingestion_time=available_time,
        open=price,
        high=price + Decimal("1"),
        low=price - Decimal("1"),
        close=price,
        volume=Decimal("1000"),
    )


def test_partial_fill_consumes_distinct_quotes_that_become_visible_together():
    decision = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    visible_at = decision + timedelta(minutes=5)
    signal = Signal(
        signal_id=UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
        instrument_id=UUID(INSTRUMENT),
        strategy_version="test",
        decision_time=decision,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.5"),
        inputs_hash="d" * 64,
    )
    assessment = RiskAssessment(
        signal_id=signal.signal_id,
        decision=RiskDecision.APPROVE,
        reason_code="TEST_APPROVED",
        approved_quantity=Decimal("10"),
    )

    result = DeterministicBacktest(
        Decimal("10000"),
        CostModel(version="test"),
        max_fill_quantity=Decimal("4"),
    ).run(
        (
            make_bar(decision, decision, "100"),
            make_bar(decision + timedelta(minutes=1), visible_at, "101"),
            make_bar(decision + timedelta(minutes=2), visible_at, "102"),
            make_bar(decision + timedelta(minutes=3), visible_at, "103"),
        ),
        lambda context: signal if context.as_of == decision else None,
        lambda _signal: assessment,
        OrderSide.BUY,
    )

    assert [event.bar.event_time for event in result.events] == [
        decision + timedelta(minutes=1),
        decision + timedelta(minutes=2),
        decision + timedelta(minutes=3),
    ]
    assert [event.result.fill.quantity for event in result.events] == [
        Decimal("4"),
        Decimal("4"),
        Decimal("2"),
    ]
    assert all(event.result.fill.fill_time == visible_at for event in result.events)
    assert len({event.result.fill.fill_id for event in result.events}) == 3
    assert result.final_state.positions[UUID(INSTRUMENT)].quantity == Decimal("10")
    assert result.unfilled_order_ids == ()
