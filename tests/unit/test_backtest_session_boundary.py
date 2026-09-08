from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from hope.application.backtests.engine import DeterministicBacktest
from hope.application.market_data.calendar import MarketSessionCalendar
from hope.domain.execution.models import OrderSide
from hope.domain.execution.simulator import CostModel
from hope.domain.market_data.models import MarketBar
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.domain.signal.models import Signal, SignalType


UTC = timezone.utc
INSTRUMENT = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"


def make_bar(instrument_id: str, event_time: datetime, available_time: datetime | None = None) -> MarketBar:
    available = event_time if available_time is None else available_time
    return MarketBar(
        instrument_id=instrument_id,
        event_time=event_time,
        available_time=available,
        ingestion_time=available,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1000"),
    )


def test_backtest_does_not_carry_delayed_quote_after_session_close_into_next_session():
    first = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    quote_event = first + timedelta(minutes=1)
    quote_available = datetime(2026, 1, 5, 15, 5, tzinfo=UTC)
    session_close = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
    next_session_open = datetime(2026, 1, 6, 14, 30, tzinfo=UTC)
    session_calendar = MarketSessionCalendar(
        (
            (first, session_close),
            (next_session_open, next_session_open + timedelta(minutes=30)),
        )
    )
    signal = Signal(
        signal_id=UUID("88888888-8888-8888-8888-888888888888"),
        instrument_id=UUID(INSTRUMENT),
        strategy_version="test",
        decision_time=first,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.5"),
        inputs_hash="6" * 64,
    )
    assessment = RiskAssessment(
        signal_id=signal.signal_id,
        decision=RiskDecision.APPROVE,
        reason_code="TEST_APPROVED",
        approved_quantity=Decimal("1"),
    )

    result = DeterministicBacktest(
        Decimal("10000"), CostModel(version="test")
    ).run(
        (
            make_bar(INSTRUMENT, first),
            make_bar(INSTRUMENT, quote_event, quote_available),
            make_bar(OTHER, next_session_open),
        ),
        lambda context: signal if context.as_of == first else None,
        lambda _signal: assessment,
        OrderSide.BUY,
        session_calendar=session_calendar,
    )

    assert result.events == ()
    assert len(result.unfilled_order_ids) == 1


def test_backtest_does_not_carry_pending_order_into_next_session():
    first = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    session_close = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
    next_session_open = datetime(2026, 1, 6, 14, 30, tzinfo=UTC)
    next_session_quote = next_session_open + timedelta(minutes=1)
    session_calendar = MarketSessionCalendar(
        (
            (first, session_close),
            (next_session_open, next_session_open + timedelta(minutes=30)),
        )
    )
    signal = Signal(
        signal_id=UUID("99999999-9999-9999-9999-999999999999"),
        instrument_id=UUID(INSTRUMENT),
        strategy_version="test",
        decision_time=first,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.5"),
        inputs_hash="5" * 64,
    )
    assessment = RiskAssessment(
        signal_id=signal.signal_id,
        decision=RiskDecision.APPROVE,
        reason_code="TEST_APPROVED",
        approved_quantity=Decimal("1"),
    )

    result = DeterministicBacktest(
        Decimal("10000"), CostModel(version="test")
    ).run(
        (
            make_bar(INSTRUMENT, first),
            make_bar(INSTRUMENT, next_session_quote),
        ),
        lambda context: signal if context.as_of == first else None,
        lambda _signal: assessment,
        OrderSide.BUY,
        session_calendar=session_calendar,
    )

    assert result.events == ()
    assert result.final_state.positions == {}
    assert len(result.unfilled_order_ids) == 1


def test_backtest_does_not_execute_when_latency_pushes_eligibility_past_session_close():
    first = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    session_close = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
    next_session_open = datetime(2026, 1, 6, 14, 30, tzinfo=UTC)
    next_session_quote = next_session_open + timedelta(minutes=1)
    session_calendar = MarketSessionCalendar(
        (
            (first, session_close),
            (next_session_open, next_session_open + timedelta(minutes=30)),
        )
    )
    signal = Signal(
        signal_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        instrument_id=UUID(INSTRUMENT),
        strategy_version="test",
        decision_time=first,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.5"),
        inputs_hash="7" * 64,
    )
    assessment = RiskAssessment(
        signal_id=signal.signal_id,
        decision=RiskDecision.APPROVE,
        reason_code="TEST_APPROVED",
        approved_quantity=Decimal("1"),
    )

    result = DeterministicBacktest(
        Decimal("10000"),
        CostModel(version="test"),
        execution_latency=timedelta(minutes=31),
    ).run(
        (
            make_bar(INSTRUMENT, first),
            make_bar(INSTRUMENT, next_session_quote),
        ),
        lambda context: signal if context.as_of == first else None,
        lambda _signal: assessment,
        OrderSide.BUY,
        session_calendar=session_calendar,
    )

    assert result.events == ()
    assert result.final_state.positions == {}
    assert len(result.unfilled_order_ids) == 1
