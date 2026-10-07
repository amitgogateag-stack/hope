"""Reconciliation receipts must not survive a failed authenticated reread."""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from hope.application.jobs import (
    JobRunStatus,
    create_job_run_completion,
    create_scheduled_job_run,
)
from hope.application.paper.effects import PaperEffectType, create_paper_effect
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository
from hope.infrastructure.repositories.paper_reconciliation_audit import (
    SqlAlchemyPaperReconciliationAuditRepository,
)


@pytest.mark.integration
def test_failed_receipt_reread_rolls_back_insert_even_when_caught(monkeypatch) -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    strategy_id, version_id = uuid4(), uuid4()
    scheduled_for = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    run = create_scheduled_job_run(
        f"paper:USA:{version_id}:receipt-savepoint",
        scheduled_for,
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text(
                "INSERT INTO strategies(strategy_id, name, family) "
                "VALUES (:id, :name, 'TEST')"
            ),
            {"id": strategy_id, "name": f"PAPER_RECEIPT_{strategy_id}"},
        )
        connection.execute(
            text(
                "INSERT INTO strategy_versions("
                "strategy_version_id, strategy_id, version, code_commit"
                ") VALUES (:version_id, :strategy_id, 'v1', 'receipt-savepoint-test')"
            ),
            {"version_id": version_id, "strategy_id": strategy_id},
        )
        jobs = SqlAlchemyJobRunRepository(connection)
        assert jobs.claim(run) is True

        effects_repository = SqlAlchemyPaperEffectRepository(connection)
        for kind in (
            PaperEffectType.SIGNAL,
            PaperEffectType.RISK,
            PaperEffectType.ORDER,
            PaperEffectType.REJECTION,
        ):
            assert effects_repository.record(
                create_paper_effect(run, kind, uuid4(), "a" * 64)
            ) is True
        assert jobs.complete(
            create_job_run_completion(
                run, JobRunStatus.SUCCEEDED, scheduled_for + timedelta(minutes=2)
            )
        ) is True
        completion = jobs.get_record_for_run(run)
        assert completion is not None
        effects = effects_repository.list_for_job_run(run.job_run_id)

        audit = SqlAlchemyPaperReconciliationAuditRepository(connection)
        with monkeypatch.context() as patch:
            patch.setattr(audit, "verify", lambda *args: False)
            with pytest.raises(
                RuntimeError, match="PAPER_RECONCILIATION_AUDIT_NOT_DURABLE"
            ):
                audit.record(completion, effects)

        # The caller caught the error but did not roll back its outer transaction.
        # The rejected receipt must not be visible or committed.
        assert connection.execute(
            text(
                "SELECT count(*) FROM audit_events "
                "WHERE event_type='PAPER_RUN_RECONCILED' AND entity_id=:id"
            ),
            {"id": str(run.job_run_id)},
        ).scalar_one() == 0

        assert audit.record(completion, effects) is True
        assert audit.verify(completion, effects) is True

    with engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT count(*) FROM audit_events "
                "WHERE event_type='PAPER_RUN_RECONCILED' AND entity_id=:id"
            ),
            {"id": str(run.job_run_id)},
        ).scalar_one() == 1
    engine.dispose()
