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
SIGNAL_INSTRUMENT = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
OTHER_INSTRUMENT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def make_bar(instrument_id: str, at: datetime, close: str = "100") -> MarketBar:
    price = Decimal(close)
    return MarketBar(
        instrument_id=instrument_id,
        event_time=at,
        available_time=at,
        ingestion_time=at,
        open=price,
        high=price + Decimal("1"),
        low=price - Decimal("1"),
        close=price,
        volume=Decimal("1000"),
    )


def test_backtest_fill_event_uses_quote_bar_provenance_when_other_bar_shares_fill_time():
    first = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    fill_time = first + timedelta(minutes=1)
    signal = Signal(
        signal_id=UUID("88888888-8888-8888-8888-888888888888"),
        instrument_id=UUID(SIGNAL_INSTRUMENT),
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

    result = DeterministicBacktest(Decimal("10000"), CostModel(version="test")).run(
        (
            make_bar(SIGNAL_INSTRUMENT, first),
            make_bar(OTHER_INSTRUMENT, fill_time),
            make_bar(SIGNAL_INSTRUMENT, fill_time),
        ),
        lambda context: signal if context.as_of == first else None,
        lambda _signal: assessment,
        OrderSide.BUY,
    )

    assert len(result.events) == 1
    assert result.events[0].bar.instrument_id == SIGNAL_INSTRUMENT
    assert result.events[0].result.fill is not None
    assert result.events[0].result.fill.instrument_id == UUID(SIGNAL_INSTRUMENT)
