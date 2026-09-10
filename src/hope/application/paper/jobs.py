from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone

from hope.application.paper.runner import PaperRuntimeContext
from hope.domain.signal.models import Signal


@dataclass(frozen=True)
class PaperSignalPersistenceJob:
    """Persist one already-decided PAPER signal through the authoritative runtime boundary."""

    signal: Signal

    def __post_init__(self) -> None:
        if not isinstance(self.signal, Signal):
            raise TypeError("PAPER_SIGNAL_JOB_REQUIRES_SIGNAL")

    def __call__(self, runtime: PaperRuntimeContext) -> None:
        decision_time = self.signal.decision_time
        if decision_time.tzinfo is None or decision_time.utcoffset() is None:
            raise ValueError("PAPER_SIGNAL_DECISION_TIME_MUST_BE_TIMEZONE_AWARE")
        if decision_time.astimezone(timezone.utc) > runtime.cycle.job_run.scheduled_for:
            raise ValueError("PAPER_SIGNAL_DECISION_AFTER_JOB_SCHEDULE")
        runtime.record_signal(self.signal)
