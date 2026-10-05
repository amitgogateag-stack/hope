import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, OperationalError

from hope.application.jobs import (
    JobRunStatus,
    create_job_run_completion,
    create_scheduled_job_run,
)
from hope.application.paper import PaperEffectType, create_paper_effect
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_effects import (
    SqlAlchemyPaperEffectRepository,
)


@pytest.mark.integration
def test_paper_effect_cannot_cross_reconciliation_terminalization() -> None:
    """A writer blocked by reconciliation cannot append after terminal truth."""
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    scheduled_for = datetime(2026, 10, 5, 2, 30, tzinfo=UTC)
    strategy_id = uuid4()
    strategy_version_id = uuid4()
    job_run = create_scheduled_job_run(
        f"paper:USA:{strategy_version_id}:reconciliation-effect-race",
        scheduled_for,
    )
    effect = create_paper_effect(
        job_run,
        PaperEffectType.SIGNAL,
        uuid4(),
        "d" * 64,
    )

    with engine.begin() as setup_connection:
        apply_migrations(setup_connection, migrations_dir)
        setup_connection.execute(
            text(
                "INSERT INTO strategies(strategy_id, name, family) "
                "VALUES (:id, :name, 'TEST')"
            ),
            {
                "id": strategy_id,
                "name": f"RECONCILIATION_EFFECT_RACE_{strategy_id}",
            },
        )
        setup_connection.execute(
            text(
                "INSERT INTO strategy_versions("
                "strategy_version_id, strategy_id, version, code_commit"
                ") VALUES ("
                ":version_id, :strategy_id, 'v1', "
                "'effect-reconciliation-concurrency'"
                ")"
            ),
            {
                "version_id": strategy_version_id,
                "strategy_id": strategy_id,
            },
        )
        assert SqlAlchemyJobRunRepository(setup_connection).claim(job_run)

    reconciliation_connection = engine.connect()
    effect_connection = engine.connect()
    reconciliation_transaction = reconciliation_connection.begin()
    try:
        jobs = SqlAlchemyJobRunRepository(reconciliation_connection)
        assert jobs.lock_claimed_for_reconciliation(job_run)

        effect_transaction = effect_connection.begin()
        try:
            effect_connection.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(OperationalError, match="lock timeout"):
                SqlAlchemyPaperEffectRepository(effect_connection).record(effect)
        finally:
            effect_transaction.rollback()

        completion = create_job_run_completion(
            job_run,
            JobRunStatus.SUCCEEDED,
            scheduled_for + timedelta(minutes=1),
        )
        assert jobs.complete(completion)
        reconciliation_transaction.commit()

        with effect_connection.begin():
            with pytest.raises(
                IntegrityError,
                match="PAPER_EFFECT_REQUIRES_CLAIMED_JOB",
            ):
                with effect_connection.begin_nested():
                    SqlAlchemyPaperEffectRepository(effect_connection).record(
                        effect
                    )

            durable = SqlAlchemyJobRunRepository(
                effect_connection
            ).get_record_for_run(job_run)
            assert durable is not None
            assert durable.status is JobRunStatus.SUCCEEDED
            assert (
                effect_connection.execute(
                    text(
                        "SELECT count(*) FROM paper_effects "
                        "WHERE effect_id = :effect_id"
                    ),
                    {"effect_id": effect.effect_id},
                ).scalar_one()
                == 0
            )
    finally:
        if reconciliation_transaction.is_active:
            reconciliation_transaction.rollback()
        reconciliation_connection.close()
        effect_connection.close()
        engine.dispose()
