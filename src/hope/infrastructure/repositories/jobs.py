from __future__ import annotations

from uuid import UUID

from sqlalchemy import Column, Connection, DateTime, MetaData, String, Table, Uuid, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hope.application.jobs.models import JobRunRecord, JobRunStatus, ScheduledJobRun


class SqlAlchemyJobRunRepository:
    """PostgreSQL-backed idempotency boundary for scheduled HOPE job runs."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        metadata = MetaData()
        self._job_runs = Table(
            "job_runs",
            metadata,
            Column("job_run_id", Uuid, primary_key=True),
            Column("job_key", String, nullable=False),
            Column("scheduled_for", DateTime(timezone=True), nullable=False),
            Column("status", String, nullable=False),
            Column("completed_at", DateTime(timezone=True), nullable=True),
            Column("failure_code", String, nullable=True),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )

    def claim(self, job_run: ScheduledJobRun) -> bool:
        """Claim one scheduled invocation; return False when it was already claimed."""
        statement = (
            pg_insert(self._job_runs)
            .values(
                job_run_id=job_run.job_run_id,
                job_key=job_run.job_key,
                scheduled_for=job_run.scheduled_for,
            )
            .on_conflict_do_nothing(index_elements=["job_key", "scheduled_for"])
            .returning(self._job_runs.c.job_run_id)
        )
        inserted_id = self._connection.execute(statement).scalar_one_or_none()
        return inserted_id is not None

    def complete(self, completion: JobRunRecord) -> bool:
        """Apply one terminal transition; return False when the run is already terminal or absent."""
        if completion.status is JobRunStatus.CLAIMED:
            raise ValueError("JOB_COMPLETION_STATUS_REQUIRED")
        statement = (
            update(self._job_runs)
            .where(
                self._job_runs.c.job_run_id == completion.run.job_run_id,
                self._job_runs.c.job_key == completion.run.job_key,
                self._job_runs.c.scheduled_for == completion.run.scheduled_for,
                self._job_runs.c.status == JobRunStatus.CLAIMED.value,
            )
            .values(
                status=completion.status.value,
                completed_at=completion.completed_at,
                failure_code=completion.failure_code,
            )
            .returning(self._job_runs.c.job_run_id)
        )
        completed_id = self._connection.execute(statement).scalar_one_or_none()
        return completed_id is not None

    def get(self, job_run_id: UUID) -> ScheduledJobRun | None:
        record = self.get_record(job_run_id)
        return record.run if record is not None else None

    def get_record(self, job_run_id: UUID) -> JobRunRecord | None:
        row = self._connection.execute(
            select(self._job_runs).where(self._job_runs.c.job_run_id == job_run_id)
        ).mappings().one_or_none()
        if row is None:
            return None
        run = ScheduledJobRun(
            job_run_id=row["job_run_id"],
            job_key=row["job_key"],
            scheduled_for=row["scheduled_for"],
        )
        return JobRunRecord(
            run=run,
            status=JobRunStatus(row["status"]),
            completed_at=row["completed_at"],
            failure_code=row["failure_code"],
        )
