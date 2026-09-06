from datetime import datetime, timezone, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.application.backtests.engine import DeterministicBacktest
from hope.domain.execution.models import OrderSide
from hope.domain.execution.simulator import CostModel
from hope.domain.market_data.models import MarketBar
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.domain.signal.models import Signal, SignalType


def bar(instrument_id, event_time, available_time, close):
    return MarketBar(
        instrument_id=str(instrument_id), event_time=event_time,
        available_time=available_time, ingestion_time=available_time,
        open=Decimal(str(close)), high=Decimal(str(close)),
        low=Decimal(str(close)), close=Decimal(str(close)), volume=Decimal("100"),
    )


def test_backtest_strategy_receives_pit_context_and_fills_only_on_future_bar():
    instrument = uuid4()
    t0 = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)
    bars = [bar(instrument, t0, t0, "100"), bar(instrument, t1, t1, "110")]
    seen = []

    def strategy(context):
        seen.append((context.as_of, tuple(b.event_time for b in context.bars)))
        if context.latest_bar is None:
            return None
        return Signal(
            signal_id=uuid4(), instrument_id=instrument, strategy_version="s1",
            decision_time=context.as_of, signal_type=SignalType.ENTRY,
            conviction=Decimal("1"), inputs_hash="a" * 64,
        )

    def risk(signal):
        return RiskAssessment(signal_id=signal.signal_id, decision=RiskDecision.APPROVE,
                              reason_code="TEST", approved_quantity=Decimal("2"))

    result = DeterministicBacktest(Decimal("1000"), CostModel("c1")).run(
        bars, strategy, risk, OrderSide.BUY
    )
    assert seen[0] == (t0, (t0,))
    assert seen[1] == (t1, (t0, t1))
    assert len(result.events) == 1
    assert result.events[0].event_time == t1
    assert result.final_state.positions[instrument].quantity == Decimal("2")


def test_backtest_pit_context_excludes_future_event_bar():
    instrument = uuid4()
    t0 = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)
    bars = [bar(instrument, t0, t0, "100"), bar(instrument, t1, t1, "999")]
    contexts = []

    def strategy(context):
        contexts.append(context)
        return None

    result = DeterministicBacktest(Decimal("1000"), CostModel("c1")).run(
        bars, strategy, lambda _signal: None, OrderSide.BUY
    )
    assert len(result.events) == 0
    assert len(contexts[0].bars) == 1
    assert contexts[0].bars[0].close == Decimal("100")


def test_backtest_rejects_signal_that_uses_unavailable_information():
    instrument = uuid4()
    event = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    available = event + timedelta(minutes=1)
    b = bar(instrument, event, available, "100")

    def strategy(context):
        return Signal(
            signal_id=uuid4(), instrument_id=instrument, strategy_version="s1",
            decision_time=event, signal_type=SignalType.ENTRY,
            conviction=Decimal("1"), inputs_hash="b" * 64,
        )

    def risk(signal):
        return RiskAssessment(signal_id=signal.signal_id, decision=RiskDecision.APPROVE,
                              reason_code="TEST", approved_quantity=Decimal("1"))

    with pytest.raises(ValueError, match="SIGNAL_USES_UNAVAILABLE_INFORMATION"):
        DeterministicBacktest(Decimal("1000"), CostModel("c1")).run(
            [b], strategy, risk, OrderSide.BUY
        )


def test_backtest_result_is_deterministic_for_same_inputs():
    instrument = uuid4()
    t0 = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)
    bars = [bar(instrument, t0, t0, "100"), bar(instrument, t1, t1, "110")]
    stable_signal_id = uuid4()

    def stable_strategy(context):
        if context.latest_bar is not None and context.latest_bar.event_time == t0:
            return Signal(signal_id=stable_signal_id, instrument_id=instrument, strategy_version="s1",
                          decision_time=context.as_of, signal_type=SignalType.ENTRY,
                          conviction=Decimal("1"), inputs_hash="d" * 64)
        return None

    def risk(signal):
        return RiskAssessment(signal_id=signal.signal_id, decision=RiskDecision.APPROVE,
                              reason_code="TEST", approved_quantity=Decimal("2"))

    first = DeterministicBacktest(Decimal("1000"), CostModel("c1")).run(bars, stable_strategy, risk, OrderSide.BUY)
    second = DeterministicBacktest(Decimal("1000"), CostModel("c1")).run(bars, stable_strategy, risk, OrderSide.BUY)
    assert first.events[0].result.order_id == second.events[0].result.order_id
    assert first.events[0].result.fill.fill_id == second.events[0].result.fill.fill_id
    assert first.valuations == second.valuations


def test_backtest_rejects_invalid_bar_instrument_id():
    t0 = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    b = MarketBar(instrument_id="not-a-uuid", event_time=t0, available_time=t0, ingestion_time=t0,
                  open=Decimal("100"), high=Decimal("100"), low=Decimal("100"), close=Decimal("100"), volume=Decimal("100"))
    instrument = uuid4()
    def strategy(_context):
        return Signal(signal_id=uuid4(), instrument_id=instrument, strategy_version="s1",
                      decision_time=t0, signal_type=SignalType.ENTRY,
                      conviction=Decimal("1"), inputs_hash="e" * 64)
    def risk(signal):
        return RiskAssessment(signal_id=signal.signal_id, decision=RiskDecision.REJECT,
                              reason_code="TEST", approved_quantity=Decimal("0"))
    with pytest.raises(ValueError, match="INVALID_BAR_INSTRUMENT_ID"):
        DeterministicBacktest(Decimal("1000"), CostModel("c1")).run([b], strategy, risk, OrderSide.BUY)


def test_backtest_records_valuation_on_every_bar_without_signals():
    instrument = uuid4()
    t0 = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)
    bars = [bar(instrument, t0, t0, "100"), bar(instrument, t1, t1, "110")]

    def no_signal(_context):
        return None

    def risk(_signal):
        raise AssertionError("risk must not run without a signal")

    result = DeterministicBacktest(Decimal("1000"), CostModel("c1")).run(
        bars, no_signal, risk, OrderSide.BUY
    )
    assert len(result.events) == 0
    assert [v.as_of for v in result.valuations] == [t0, t1]
    assert [v.equity for v in result.valuations] == [Decimal("1000"), Decimal("1000")]


def test_backtest_marks_existing_position_with_latest_bar_price():
    instrument = uuid4()
    t0 = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)
    bars = [bar(instrument, t0, t0, "100"), bar(instrument, t1, t1, "110")]
    seen = []

    def strategy(context):
        current = context.latest_bar
        assert current is not None
        if current.event_time == t0:
            return Signal(
                signal_id=uuid4(), instrument_id=instrument, strategy_version="s1",
                decision_time=context.as_of, signal_type=SignalType.ENTRY,
                conviction=Decimal("1"), inputs_hash="f" * 64,
            )
        seen.append(current.event_time)
        return None

    def risk(signal):
        return RiskAssessment(signal_id=signal.signal_id, decision=RiskDecision.APPROVE,
                              reason_code="TEST", approved_quantity=Decimal("2"))

    result = DeterministicBacktest(Decimal("1000"), CostModel("c1")).run(
        bars, strategy, risk, OrderSide.BUY
    )
    assert seen == [t1]
    assert len(result.valuations) == 2
    assert result.valuations[0].equity == Decimal("1000")
    # The order is filled on t1 at the t1 quote; the position is therefore
    # marked at its execution price and has no immediate unrealized gain.
    assert result.valuations[1].equity == Decimal("1000")


def test_backtest_result_contains_deterministic_performance_metrics():
    instrument = uuid4()
    t0 = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)
    bars = [bar(instrument, t0, t0, "100"), bar(instrument, t1, t1, "110")]

    def strategy(context):
        current = context.latest_bar
        if current is not None and current.event_time == t0:
            return Signal(
                signal_id=uuid4(), instrument_id=instrument, strategy_version="s1",
                decision_time=context.as_of, signal_type=SignalType.ENTRY,
                conviction=Decimal("1"), inputs_hash="a" * 64,
            )
        return None

    def risk(signal):
        return RiskAssessment(signal_id=signal.signal_id, decision=RiskDecision.APPROVE,
                              reason_code="TEST", approved_quantity=Decimal("2"))

    result = DeterministicBacktest(Decimal("1000"), CostModel("c1")).run(
        bars, strategy, risk, OrderSide.BUY
    )
    assert result.metrics.initial_equity == Decimal("1000")
    assert result.metrics.final_equity == Decimal("1000")
    assert result.metrics.total_return == Decimal("0")
    assert result.metrics.max_drawdown == Decimal("0")


def test_backtest_latency_waits_for_future_eligible_quote():
    instrument = uuid4()
    t0 = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)
    t2 = t0 + timedelta(minutes=2)
    bars = [bar(instrument, t0, t0, "100"), bar(instrument, t1, t1, "105"), bar(instrument, t2, t2, "110")]

    def strategy(context):
        if context.latest_bar is not None and context.latest_bar.event_time == t0:
            return Signal(
                signal_id=uuid4(), instrument_id=instrument, strategy_version="s1",
                decision_time=context.as_of, signal_type=SignalType.ENTRY,
                conviction=Decimal("1"), inputs_hash="a" * 64,
            )
        return None

    def risk(signal):
        return RiskAssessment(signal_id=signal.signal_id, decision=RiskDecision.APPROVE,
                              reason_code="TEST", approved_quantity=Decimal("1"))

    result = DeterministicBacktest(
        Decimal("1000"), CostModel("c1"), execution_latency=timedelta(minutes=2)
    ).run(bars, strategy, risk, OrderSide.BUY)
    assert len(result.events) == 1
    assert result.events[0].event_time == t2
    assert result.events[0].result.fill.price == Decimal("110")


def test_backtest_rejects_out_of_order_bars():
    instrument = uuid4()
    t0 = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)
    bars = [bar(instrument, t1, t1, "101"), bar(instrument, t0, t0, "100")]

    with pytest.raises(ValueError, match="BACKTEST_EVENTS_MUST_BE_NON_DECREASING"):
        DeterministicBacktest(Decimal("1000"), CostModel("c1")).run(
            bars, lambda _context: None, lambda _signal: None, OrderSide.BUY
        )
