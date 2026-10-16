from __future__ import annotations

from datetime import UTC

from uuid import UUID

from sqlalchemy import Column, Connection, DateTime, MetaData, String, Table, Uuid, select, text, update
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
        if inserted_id is not None:
            return True

        existing_id = self._connection.execute(
            select(self._job_runs.c.job_run_id).where(
                self._job_runs.c.job_key == job_run.job_key,
                self._job_runs.c.scheduled_for == job_run.scheduled_for,
            )
        ).scalar_one()
        if existing_id != job_run.job_run_id:
            raise ValueError("JOB_RUN_IDENTITY_CONFLICT")
        return False

    def lock_claimed(self, job_run: ScheduledJobRun) -> bool:
        """Lock and authenticate a claimed run before related durable effects."""
        row = self._connection.execute(
            select(
                self._job_runs.c.job_run_id,
                self._job_runs.c.job_key,
                self._job_runs.c.scheduled_for,
                self._job_runs.c.status,
            )
            .where(self._job_runs.c.job_run_id == job_run.job_run_id)
            .with_for_update()
        ).mappings().one_or_none()
        if row is None:
            return False
        if row["job_key"] != job_run.job_key or row["scheduled_for"].astimezone(UTC) != job_run.scheduled_for.astimezone(UTC):
            raise ValueError("JOB_RUN_IDENTITY_CONFLICT")
        return row["status"] == JobRunStatus.CLAIMED.value

    def lock_claimed_for_reconciliation(self, job_run: ScheduledJobRun) -> bool:
        """Lock a claimed run so PAPER effect writers cannot race reconciliation proof."""
        return self.lock_claimed(job_run)

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
        if completed_id is not None:
            return True

        existing_by_id = self._connection.execute(
            select(
                self._job_runs.c.job_key,
                self._job_runs.c.scheduled_for,
            ).where(self._job_runs.c.job_run_id == completion.run.job_run_id)
        ).mappings().one_or_none()
        if existing_by_id is not None:
            if (
                existing_by_id["job_key"] != completion.run.job_key
                or existing_by_id["scheduled_for"].astimezone(UTC) != completion.run.scheduled_for.astimezone(UTC)
            ):
                raise ValueError("JOB_RUN_IDENTITY_CONFLICT")
            return False

        existing_id = self._connection.execute(
            select(self._job_runs.c.job_run_id).where(
                self._job_runs.c.job_key == completion.run.job_key,
                self._job_runs.c.scheduled_for == completion.run.scheduled_for,
            )
        ).scalar_one_or_none()
        if existing_id is not None and existing_id != completion.run.job_run_id:
            raise ValueError("JOB_RUN_IDENTITY_CONFLICT")
        return False

    def quarantine_empty_claim(self, completion: JobRunRecord) -> bool:
        """Preserve recoverable evidence and roll back rejected transitions.

        PAPER effect inserts lock this same job row. The lock is retained until
        the caller commits or rolls back, so a writer cannot cross the empty
        check and append an effect after quarantine. No trading writer runs here.
        """
        if (
            completion.status is not JobRunStatus.FAILED
            or completion.failure_code != "PAPER_JOB_INCOMPLETE_PRIOR_CLAIM"
        ):
            raise ValueError("PAPER_JOB_QUARANTINE_COMPLETION_INVALID")
        with self._connection.begin_nested():
            isolation = self._connection.execute(
                text("SELECT current_setting('transaction_isolation')")
            ).scalar_one()
            if isolation != "read committed":
                raise RuntimeError("PAPER_JOB_QUARANTINE_REQUIRES_READ_COMMITTED")
            claimed = self.lock_claimed(completion.run)
            durable = self.get_record_for_run(completion.run)
            if durable is None:
                raise RuntimeError("PAPER_JOB_CLAIM_STATE_MISSING")
            if not claimed:
                return False
            has_effects = self._connection.execute(
                text("SELECT EXISTS (SELECT 1 FROM paper_effects WHERE job_run_id=:id)"),
                {"id": completion.run.job_run_id},
            ).scalar_one()
            if has_effects:
                raise RuntimeError("PAPER_JOB_QUARANTINE_REQUIRES_RECONCILIATION")
            if not self.complete(completion):
                raise RuntimeError("PAPER_JOB_COMPLETION_CONFLICT")
            if self.get_record_for_run(completion.run) != completion:
                raise RuntimeError("PAPER_JOB_QUARANTINE_NOT_DURABLE")
            return True

    def get_record_for_run(self, job_run: ScheduledJobRun) -> JobRunRecord | None:
        """Resolve persisted state by both durable-ID and schedule identity, failing on mismatch."""
        existing_by_id = self._connection.execute(
            select(
                self._job_runs.c.job_key,
                self._job_runs.c.scheduled_for,
            ).where(self._job_runs.c.job_run_id == job_run.job_run_id)
        ).mappings().one_or_none()
        if existing_by_id is not None:
            if (
                existing_by_id["job_key"] != job_run.job_key
                or existing_by_id["scheduled_for"].astimezone(UTC) != job_run.scheduled_for.astimezone(UTC)
            ):
                raise ValueError("JOB_RUN_IDENTITY_CONFLICT")
            return self.get_record(job_run.job_run_id)

        existing_id = self._connection.execute(
            select(self._job_runs.c.job_run_id).where(
                self._job_runs.c.job_key == job_run.job_key,
                self._job_runs.c.scheduled_for == job_run.scheduled_for,
            )
        ).scalar_one_or_none()
        if existing_id is None:
            return None
        if existing_id != job_run.job_run_id:
            raise ValueError("JOB_RUN_IDENTITY_CONFLICT")
        return self.get_record(job_run.job_run_id)

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
        if row["job_key"] != run.job_key:
            raise ValueError("JOB_RUN_KEY_NOT_CANONICAL")
        record = JobRunRecord(
            run=run,
            status=JobRunStatus(row["status"]),
            completed_at=row["completed_at"],
            failure_code=row["failure_code"],
        )
        if row["failure_code"] != record.failure_code:
            raise ValueError("JOB_RUN_FAILURE_CODE_NOT_CANONICAL")
        return record
