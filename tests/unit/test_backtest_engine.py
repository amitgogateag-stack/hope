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


def test_deterministic_backtest_processes_current_bar_only():
    instrument = uuid4()
    t0 = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    bars = [bar(instrument, t0, t0, "100")]
    seen = []

    def strategy(current):
        seen.append(current.event_time)
        return Signal(
            signal_id=uuid4(), instrument_id=instrument, strategy_version="s1",
            decision_time=current.available_time, signal_type=SignalType.ENTRY,
            conviction=Decimal("1"), inputs_hash="a" * 64,
        )

    def risk(signal):
        return RiskAssessment(signal_id=signal.signal_id, decision=RiskDecision.APPROVE,
                              reason_code="TEST", approved_quantity=Decimal("2"))

    result = DeterministicBacktest(Decimal("1000"), CostModel("c1")).run(
        bars, strategy, risk, OrderSide.BUY
    )
    assert seen == [t0]
    assert result.final_state.positions[instrument].quantity == Decimal("2")
    assert result.final_state.cash == Decimal("800")
    assert len(result.valuations) == 1
    assert result.valuations[0].equity == Decimal("1000")


def test_backtest_rejects_signal_that_uses_unavailable_information():
    instrument = uuid4()
    event = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    available = event + timedelta(minutes=1)
    b = bar(instrument, event, available, "100")

    def strategy(current):
        return Signal(
            signal_id=uuid4(), instrument_id=instrument, strategy_version="s1",
            decision_time=current.event_time, signal_type=SignalType.ENTRY,
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
    bars = [bar(instrument, t0, t0, "100")]

    def strategy(current):
        return Signal(signal_id=uuid4(), instrument_id=instrument, strategy_version="s1",
                      decision_time=current.available_time, signal_type=SignalType.ENTRY,
                      conviction=Decimal("1"), inputs_hash="c" * 64)

    def risk(signal):
        return RiskAssessment(signal_id=signal.signal_id, decision=RiskDecision.APPROVE,
                              reason_code="TEST", approved_quantity=Decimal("2"))

    # The signal ID is generated per invocation, so use a stable strategy fixture instead.
    stable_signal_id = uuid4()
    def stable_strategy(current):
        return Signal(signal_id=stable_signal_id, instrument_id=instrument, strategy_version="s1",
                      decision_time=current.available_time, signal_type=SignalType.ENTRY,
                      conviction=Decimal("1"), inputs_hash="d" * 64)

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
    def strategy(current):
        return Signal(signal_id=uuid4(), instrument_id=instrument, strategy_version="s1",
                      decision_time=current.available_time, signal_type=SignalType.ENTRY,
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

    def no_signal(_current):
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

    def strategy(current):
        if current.event_time == t0:
            return Signal(
                signal_id=uuid4(), instrument_id=instrument, strategy_version="s1",
                decision_time=current.available_time, signal_type=SignalType.ENTRY,
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
    assert result.valuations[1].equity == Decimal("1020")


def test_backtest_result_contains_deterministic_performance_metrics():
    instrument = uuid4()
    t0 = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)
    bars = [bar(instrument, t0, t0, "100"), bar(instrument, t1, t1, "110")]

    def strategy(current):
        if current.event_time == t0:
            return Signal(
                signal_id=uuid4(), instrument_id=instrument, strategy_version="s1",
                decision_time=current.available_time, signal_type=SignalType.ENTRY,
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
    assert result.metrics.final_equity == Decimal("1020")
    assert result.metrics.total_return == Decimal("0.02")
    assert result.metrics.max_drawdown == Decimal("0")


def test_backtest_rejects_out_of_order_bars():
    instrument = uuid4()
    t0 = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)
    bars = [bar(instrument, t1, t1, "101"), bar(instrument, t0, t0, "100")]

    with pytest.raises(ValueError, match="BACKTEST_EVENTS_MUST_BE_NON_DECREASING"):
        DeterministicBacktest(Decimal("1000"), CostModel("c1")).run(
            bars, lambda _current: None, lambda _signal: None, OrderSide.BUY
        )
