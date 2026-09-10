from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper.context import PaperCycleContext
from hope.application.paper.jobs import PaperSignalPersistenceJob
from hope.domain.signal.models import Signal, SignalType


UTC = timezone.utc
INPUTS_HASH = "a" * 64


class _Runtime:
    def __init__(self, job_run) -> None:
        self.cycle = PaperCycleContext(job_run)
        self.recorded = []

    def record_signal(self, signal: Signal) -> bool:
        self.recorded.append(signal)
        return True


def _signal(decision_time: datetime) -> Signal:
    return Signal(
        signal_id=uuid4(),
        instrument_id=uuid4(),
        strategy_version="paper-signal-job-v1",
        decision_time=decision_time,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.7"),
        inputs_hash=INPUTS_HASH,
    )


def test_paper_signal_persistence_job_requires_signal_model() -> None:
    with pytest.raises(TypeError, match="PAPER_SIGNAL_JOB_REQUIRES_SIGNAL"):
        PaperSignalPersistenceJob(object())


def test_paper_signal_persistence_job_records_decided_signal() -> None:
    scheduled_for = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    job_run = create_scheduled_job_run("paper-signal-persist", scheduled_for)
    signal = _signal(datetime(2026, 9, 10, 13, 59, tzinfo=UTC))
    runtime = _Runtime(job_run)

    PaperSignalPersistenceJob(signal)(runtime)

    assert runtime.recorded == [signal]


def test_paper_signal_persistence_job_rejects_naive_decision_time() -> None:
    scheduled_for = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    job_run = create_scheduled_job_run("paper-signal-persist", scheduled_for)
    runtime = _Runtime(job_run)

    with pytest.raises(ValueError, match="PAPER_SIGNAL_DECISION_TIME_MUST_BE_TIMEZONE_AWARE"):
        PaperSignalPersistenceJob(_signal(datetime(2026, 9, 10, 13, 59)))(runtime)

    assert runtime.recorded == []


def test_paper_signal_persistence_job_rejects_future_decision() -> None:
    scheduled_for = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    job_run = create_scheduled_job_run("paper-signal-persist", scheduled_for)
    runtime = _Runtime(job_run)

    with pytest.raises(ValueError, match="PAPER_SIGNAL_DECISION_AFTER_JOB_SCHEDULE"):
        PaperSignalPersistenceJob(
            _signal(datetime(2026, 9, 10, 14, 0, 1, tzinfo=UTC))
        )(runtime)

    assert runtime.recorded == []
