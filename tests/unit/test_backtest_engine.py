from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from hope.application.backtests.engine import BacktestDataQualityError, DeterministicBacktest
from hope.domain.execution.models import OrderSide
from hope.domain.execution.simulator import CostModel
from hope.domain.market_data.models import MarketBar
from hope.domain.risk.models import RiskAssessment
from hope.domain.signal.models import Signal, SignalType
from uuid import UUID


UTC = timezone.utc
INSTRUMENT = "11111111-1111-1111-1111-111111111111"


def bar(at: datetime) -> MarketBar:
    return MarketBar(
        instrument_id=INSTRUMENT,
        event_time=at,
        available_time=at,
        ingestion_time=at,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1000"),
    )


def backtest() -> DeterministicBacktest:
    return DeterministicBacktest(Decimal("10000"), CostModel(version="test"))


def no_strategy(_context):
    return None


def no_risk(_signal):
    raise AssertionError("risk must not run when data-quality gate fails")


def test_backtest_rejects_empty_market_data_before_strategy_execution():
    with pytest.raises(BacktestDataQualityError) as exc:
        backtest().run([], no_strategy, no_risk, OrderSide.BUY)

    assert exc.value.report.empty_input is True
    assert exc.value.report.safe is False


def test_backtest_rejects_duplicate_market_data_before_strategy_execution():
    timestamp = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    bars = [bar(timestamp), bar(timestamp)]

    with pytest.raises(BacktestDataQualityError) as exc:
        backtest().run(bars, no_strategy, no_risk, OrderSide.BUY)

    assert exc.value.report.duplicate_keys == ((INSTRUMENT, timestamp),)
    assert exc.value.report.safe is False


def test_backtest_accepts_valid_ordered_market_data():
    first = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    bars = [bar(first), bar(first + timedelta(minutes=1))]

    result = backtest().run(bars, no_strategy, no_risk, OrderSide.BUY)

    assert len(result.valuations) == 2
    assert result.unfilled_order_ids == ()


def test_backtest_rejects_signal_decision_after_context_availability():
    first = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    signal = Signal(
        signal_id=UUID("22222222-2222-2222-2222-222222222222"),
        instrument_id=UUID(INSTRUMENT),
        strategy_version="test",
        decision_time=first + timedelta(minutes=1),
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.5"),
        inputs_hash="0" * 64,
    )

    def future_decision(_context):
        return signal

    with pytest.raises(ValueError, match="SIGNAL_DECISION_AFTER_CONTEXT"):
        backtest().run((bar(first),), future_decision, no_risk, OrderSide.BUY)
