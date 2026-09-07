from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.domain.execution import ExecutionQuote


def test_execution_quote_rejects_availability_before_event_time():
    event_time = datetime(2026, 1, 1, 14, 1, tzinfo=timezone.utc)
    available_time = datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="QUOTE_AVAILABLE_TIME_PRECEDES_EVENT_TIME"):
        ExecutionQuote(
            uuid4(),
            event_time,
            Decimal("99"),
            Decimal("100"),
            available_time=available_time,
        )
