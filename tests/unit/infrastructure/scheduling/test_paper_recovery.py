from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from hope.application.jobs import JobRunStatus, create_scheduled_job_run
from hope.infrastructure.scheduling import recovery
from hope.infrastructure.scheduling.recovery import (
    PaperRecoveryDisposition,
    assess_due_paper_recovery,
)


def test_recovery_assessment_classifies_full_restart_matrix(monkeypatch) -> None:
    current = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
    fresh = create_scheduled_job_run("paper-fresh", current - timedelta(minutes=1))
    terminal = create_scheduled_job_run("paper-terminal", current - timedelta(minutes=20))
    incomplete = create_scheduled_job_run("paper-incomplete", current - timedelta(minutes=2))
    stale = create_scheduled_job_run("paper-stale", current - timedelta(minutes=6))

    records = {
        terminal.job_run_id: SimpleNamespace(status=JobRunStatus.SUCCEEDED),
        incomplete.job_run_id: SimpleNamespace(status=JobRunStatus.CLAIMED),
    }

    class FakeRepository:
        def __init__(self, connection):
            self.connection = connection

        def get_record_for_run(self, job_run):
            return records.get(job_run.job_run_id)

    monkeypatch.setattr(recovery, "SqlAlchemyJobRunRepository", FakeRepository)
    report = assess_due_paper_recovery(
        object(),
        [fresh, terminal, incomplete, stale],
        current=current,
        max_lateness=timedelta(minutes=5),
    )

    dispositions = {item.job_run_id: item.disposition for item in report.assessments}
    assert dispositions == {
        fresh.job_run_id: PaperRecoveryDisposition.FRESH,
        terminal.job_run_id: PaperRecoveryDisposition.TERMINAL,
        incomplete.job_run_id: PaperRecoveryDisposition.INCOMPLETE,
        stale.job_run_id: PaperRecoveryDisposition.STALE,
    }
    assert report.terminal_run_ids == frozenset({terminal.job_run_id})
    assert report.incomplete_run_ids == frozenset({incomplete.job_run_id})
    assert report.stale_run_ids == frozenset({stale.job_run_id})


def test_recovery_report_fails_closed_on_incomplete_before_stale(monkeypatch) -> None:
    current = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
    incomplete = create_scheduled_job_run("paper-incomplete", current - timedelta(minutes=2))
    stale = create_scheduled_job_run("paper-stale", current - timedelta(minutes=6))

    class FakeRepository:
        def __init__(self, connection):
            pass

        def get_record_for_run(self, job_run):
            if job_run.job_run_id == incomplete.job_run_id:
                return SimpleNamespace(status=JobRunStatus.CLAIMED)
            return None

    monkeypatch.setattr(recovery, "SqlAlchemyJobRunRepository", FakeRepository)
    report = assess_due_paper_recovery(
        object(),
        [stale, incomplete],
        current=current,
        max_lateness=timedelta(minutes=5),
    )

    with pytest.raises(RuntimeError, match="PAPER_JOB_INCOMPLETE_PRIOR_CLAIM"):
        report.assert_safe_to_execute()
