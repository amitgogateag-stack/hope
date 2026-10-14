from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper.context import PaperCycleContext
from hope.application.paper.terminals import PaperTerminalWriter, paper_terminal_payload_hash
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


class _TerminalRepository:
    def __init__(self) -> None:
        self.calls = []

    def persist(self, effect, outcome) -> bool:
        self.calls.append((effect, outcome))
        return True


@pytest.mark.parametrize("kind", ["CANCELLED", "REJECTED"])
def test_terminal_writer_rejects_post_schedule_outcome_before_persistence(kind: str) -> None:
    scheduled_for = datetime(2026, 9, 9, 20, 0, tzinfo=timezone.utc)
    context = PaperCycleContext(
        create_scheduled_job_run("paper-terminal-writer", scheduled_for)
    )
    event_time = scheduled_for + timedelta(microseconds=1)
    order_id, signal_id, instrument_id = uuid4(), uuid4(), uuid4()
    if kind == "CANCELLED":
        outcome = ExecutionCancellation(
            order_id,
            signal_id,
            instrument_id,
            Environment.PAPER,
            "TEST_CANCEL",
            event_time,
            Decimal("2"),
        )
    else:
        outcome = ExecutionRejection(
            order_id,
            signal_id,
            instrument_id,
            Environment.PAPER,
            "TEST_REJECT",
            event_time,
        )
    repository = _TerminalRepository()

    with pytest.raises(ValueError, match="PAPER_TERMINAL_AFTER_JOB_SCHEDULE"):
        PaperTerminalWriter(repository).record(context, outcome)

    assert repository.calls == []
