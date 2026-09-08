from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from hope.application.backtests.engine import DeterministicBacktest
from hope.domain.execution.models import OrderSide
from hope.domain.execution.simulator import CostModel
from hope.domain.market_data.models import MarketBar
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.domain.signal.models import Signal, SignalType

UTC = timezone.utc
INSTRUMENT = "11111111-1111-1111-1111-111111111111"


def test_backtest_rejects_duplicate_signal_id_after_risk_rejection():
    first = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    second = first + timedelta(minutes=1)
    bars = (
        MarketBar(
            instrument_id=INSTRUMENT,
            event_time=first,
            available_time=first,
            ingestion_time=first,
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=Decimal("1000"),
        ),
        MarketBar(
            instrument_id=INSTRUMENT,
            event_time=second,
            available_time=second,
            ingestion_time=second,
            open=Decimal("101"),
            high=Decimal("102"),
            low=Decimal("100"),
            close=Decimal("101"),
            volume=Decimal("1000"),
        ),
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
        decision=RiskDecision.REJECT,
        reason_code="TEST_REJECTED",
        approved_quantity=Decimal("0"),
    )

    with pytest.raises(ValueError, match="DUPLICATE_SIGNAL_ID"):
        DeterministicBacktest(Decimal("10000"), CostModel(version="test")).run(
            bars,
            lambda _context: signal,
            lambda _signal: assessment,
            OrderSide.BUY,
        )
