from datetime import datetime, timedelta, timezone

import pytest

from hope.application.jobs import create_scheduled_job_run
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository


UTC = timezone.utc


class _MappingResult:
    def __init__(self, row: dict[str, object]) -> None:
        self._row = row

    def mappings(self) -> "_MappingResult":
        return self

    def one_or_none(self) -> dict[str, object]:
        return self._row


class _Connection:
    def __init__(self, row: dict[str, object]) -> None:
        self._row = row

    def execute(self, _statement: object) -> _MappingResult:
        return _MappingResult(self._row)


def _durable_row() -> dict[str, object]:
    run = create_scheduled_job_run(
        "paper-cycle-read-defense",
        datetime(2026, 9, 9, 21, 0, tzinfo=UTC),
    )
    return {
        "job_run_id": run.job_run_id,
        "job_key": run.job_key,
        "scheduled_for": run.scheduled_for,
        "status": "FAILED",
        "completed_at": run.scheduled_for + timedelta(minutes=1),
        "failure_code": "UPSTREAM_DATA_UNAVAILABLE",
        "created_at": run.scheduled_for,
    }


def test_job_run_read_rejects_noncanonical_durable_key() -> None:
    row = _durable_row()
    row["job_key"] = f" {row['job_key']} "
    repository = SqlAlchemyJobRunRepository(_Connection(row))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="JOB_RUN_KEY_NOT_CANONICAL"):
        repository.get_record(row["job_run_id"])  # type: ignore[arg-type]


def test_job_run_read_rejects_noncanonical_durable_failure_code() -> None:
    row = _durable_row()
    row["failure_code"] = f" {row['failure_code']} "
    repository = SqlAlchemyJobRunRepository(_Connection(row))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="JOB_RUN_FAILURE_CODE_NOT_CANONICAL"):
        repository.get_record(row["job_run_id"])  # type: ignore[arg-type]
