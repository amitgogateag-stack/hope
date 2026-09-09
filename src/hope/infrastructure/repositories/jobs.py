from __future__ import annotations

from uuid import UUID

from sqlalchemy import Column, Connection, DateTime, MetaData, String, Table, Uuid, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hope.application.jobs.models import ScheduledJobRun


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

    def get(self, job_run_id: UUID) -> ScheduledJobRun | None:
        row = self._connection.execute(
            select(self._job_runs).where(self._job_runs.c.job_run_id == job_run_id)
        ).mappings().one_or_none()
        if row is None:
            return None
        return ScheduledJobRun(
            job_run_id=row["job_run_id"],
            job_key=row["job_key"],
            scheduled_for=row["scheduled_for"],
        )
