from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.domain.signal.models import Signal, SignalType


def _signal(decision_time: datetime) -> Signal:
    return Signal(
        signal_id=uuid4(),
        instrument_id=uuid4(),
        strategy_version="signal-time-v1",
        decision_time=decision_time,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.5"),
        inputs_hash="a" * 64,
    )


def test_signal_requires_timezone_aware_decision_time() -> None:
    with pytest.raises(ValueError, match="SIGNAL_DECISION_TIME_MUST_BE_TIMEZONE_AWARE"):
        _signal(datetime(2026, 9, 14, 8, 0))


def test_signal_point_in_time_check_requires_aware_availability_time() -> None:
    signal = _signal(datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc))

    with pytest.raises(ValueError, match="timestamps must be timezone-aware"):
        signal.assert_point_in_time(datetime(2026, 9, 14, 7, 59))
