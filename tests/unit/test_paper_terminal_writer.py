from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.application.paper.terminals import paper_terminal_payload_hash
from hope.domain.execution.models import (
    Environment,
    ExecutionCancellation,
    ExecutionRejection,
)


@pytest.mark.parametrize("kind", ["CANCELLED", "REJECTED"])
def test_terminal_payload_hash_canonicalizes_same_instant_to_utc(kind: str) -> None:
    order_id, signal_id, instrument_id = uuid4(), uuid4(), uuid4()
    utc_time = datetime(2026, 9, 9, 20, 1, 2, 3456, tzinfo=timezone.utc)
    offset_time = utc_time.astimezone(timezone(timedelta(hours=-6)))

    def outcome(event_time: datetime):
        if kind == "CANCELLED":
            return ExecutionCancellation(
                order_id,
                signal_id,
                instrument_id,
                Environment.PAPER,
                "TEST_CANCEL",
                event_time,
                Decimal("2.00"),
            )
        return ExecutionRejection(
            order_id,
            signal_id,
            instrument_id,
            Environment.PAPER,
            "TEST_REJECT",
            event_time,
        )

    assert paper_terminal_payload_hash(outcome(utc_time)) == (
        paper_terminal_payload_hash(outcome(offset_time))
    )
