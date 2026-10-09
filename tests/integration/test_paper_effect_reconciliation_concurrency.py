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


@pytest.mark.integration
def test_restarted_effect_writer_cannot_append_after_terminal_run() -> None:
    """A fresh connection after restart cannot resume effects on a completed job."""
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    scheduled_for = datetime(2026, 10, 9, 14, 0, tzinfo=UTC)
    strategy_id, version_id = uuid4(), uuid4()
    run = create_scheduled_job_run(
        f"paper:USA:{version_id}:restart-terminal-guard", scheduled_for,
    )
    first = create_paper_effect(run, PaperEffectType.SIGNAL, uuid4(), "a" * 64)
    late = create_paper_effect(run, PaperEffectType.RISK, uuid4(), "b" * 64)

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text(
                "INSERT INTO strategies(strategy_id, name, family) "
                "VALUES (:id, :name, 'TEST')"
            ),
            {"id": strategy_id, "name": f"RESTART_GUARD_{strategy_id}"},
        )
        connection.execute(
            text(
                "INSERT INTO strategy_versions("
                "strategy_version_id, strategy_id, version, code_commit"
                ") VALUES (:id, :strategy_id, 'v1', 'restart-terminal-guard')"
            ),
            {"id": version_id, "strategy_id": strategy_id},
        )
        jobs = SqlAlchemyJobRunRepository(connection)
        assert jobs.claim(run)
        assert SqlAlchemyPaperEffectRepository(connection).record(first)
        assert jobs.complete(
            create_job_run_completion(
                run, JobRunStatus.SUCCEEDED,
                scheduled_for + timedelta(minutes=1),
            )
        )

    # Reopen after the previous transaction and connection have closed.
    with engine.begin() as connection:
        effects = SqlAlchemyPaperEffectRepository(connection)
        assert effects.get_recoverable(PaperEffectType.SIGNAL, first.entity_id) == first
        with pytest.raises(IntegrityError, match="PAPER_EFFECT_REQUIRES_CLAIMED_JOB"):
            with connection.begin_nested():
                effects.record(late)
        assert effects.list_for_job_run(run.job_run_id) == (first,)
        assert connection.execute(
            text("SELECT count(*) FROM paper_effects WHERE job_run_id=:id"),
            {"id": run.job_run_id},
        ).scalar_one() == 1
    engine.dispose()


@pytest.mark.integration
def test_failed_job_effect_cannot_be_recovered_or_reused_by_another_run() -> None:
    """An immutable effect from FAILED work is never valid restart evidence."""
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    scheduled_for = datetime(2026, 10, 9, 15, 0, tzinfo=UTC)
    strategy_id, version_id = uuid4(), uuid4()
    first = create_scheduled_job_run(
        f"paper:USA:{version_id}:failed-owner-first", scheduled_for,
    )
    second = create_scheduled_job_run(
        f"paper:USA:{version_id}:failed-owner-second",
        scheduled_for + timedelta(minutes=1),
    )
    effect = create_paper_effect(
        first, PaperEffectType.SIGNAL, uuid4(), "e" * 64,
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text("INSERT INTO strategies(strategy_id, name, family) "
                 "VALUES (:id, :name, 'TEST')"),
            {"id": strategy_id, "name": f"FAILED_OWNER_{strategy_id}"},
        )
        connection.execute(
            text("INSERT INTO strategy_versions("
                 "strategy_version_id, strategy_id, version, code_commit"
                 ") VALUES (:id, :strategy_id, 'v1', 'failed-owner-recovery')"),
            {"id": version_id, "strategy_id": strategy_id},
        )
        jobs = SqlAlchemyJobRunRepository(connection)
        assert jobs.claim(first)
        assert SqlAlchemyPaperEffectRepository(connection).record(effect)
        assert jobs.complete(
            create_job_run_completion(
                first, JobRunStatus.FAILED,
                scheduled_for + timedelta(seconds=30),
                failure_code="EXPECTED_FAILED_OWNER",
            )
        )
        assert jobs.claim(second)

    with engine.begin() as connection:
        effects = SqlAlchemyPaperEffectRepository(connection)
        with pytest.raises(ValueError, match="PAPER_EFFECT_OWNER_NOT_RECOVERABLE"):
            effects.get_recoverable(effect.effect_type, effect.entity_id)
        with pytest.raises(ValueError, match="PAPER_EFFECT_IDENTITY_CONFLICT"):
            effects.get_reusable_for_job(
                effect.effect_type, effect.entity_id, second.job_run_id,
            )
        assert effects.get(effect.effect_type, effect.entity_id) == effect
        assert SqlAlchemyJobRunRepository(connection).complete(
            create_job_run_completion(
                second, JobRunStatus.FAILED,
                scheduled_for + timedelta(minutes=2),
                failure_code="EXPECTED_FAILED_OWNER_REUSE_TEST",
            )
        )
    engine.dispose()


@pytest.mark.integration
def test_claimed_owner_effect_cannot_be_reused_by_concurrent_job() -> None:
    """An in-flight owner's durable effect cannot become another job's evidence."""
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    scheduled_for = datetime(2026, 10, 9, 15, 30, tzinfo=UTC)
    strategy_id, version_id = uuid4(), uuid4()
    first = create_scheduled_job_run(
        f"paper:USA:{version_id}:claimed-owner-first", scheduled_for,
    )
    second = create_scheduled_job_run(
        f"paper:USA:{version_id}:claimed-owner-second",
        scheduled_for + timedelta(minutes=1),
    )
    effect = create_paper_effect(
        first, PaperEffectType.SIGNAL, uuid4(), "f" * 64,
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text("INSERT INTO strategies(strategy_id, name, family) "
                 "VALUES (:id, :name, 'TEST')"),
            {"id": strategy_id, "name": f"CLAIMED_OWNER_{strategy_id}"},
        )
        connection.execute(
            text("INSERT INTO strategy_versions("
                 "strategy_version_id, strategy_id, version, code_commit"
                 ") VALUES (:id, :strategy_id, 'v1', 'claimed-owner-recovery')"),
            {"id": version_id, "strategy_id": strategy_id},
        )
        jobs = SqlAlchemyJobRunRepository(connection)
        assert jobs.claim(first)
        assert SqlAlchemyPaperEffectRepository(connection).record(effect)
        assert jobs.claim(second)

    with engine.begin() as connection:
        effects = SqlAlchemyPaperEffectRepository(connection)
        with pytest.raises(ValueError, match="PAPER_EFFECT_OWNER_NOT_RECOVERABLE"):
            effects.get_recoverable(effect.effect_type, effect.entity_id)
        with pytest.raises(ValueError, match="PAPER_EFFECT_IDENTITY_CONFLICT"):
            effects.get_reusable_for_job(
                effect.effect_type, effect.entity_id, second.job_run_id,
            )
        assert effects.get_reusable_for_job(
            effect.effect_type, effect.entity_id, first.job_run_id,
        ) == effect

        jobs = SqlAlchemyJobRunRepository(connection)
        for run in (first, second):
            assert jobs.complete(
                create_job_run_completion(
                    run, JobRunStatus.FAILED,
                    scheduled_for + timedelta(minutes=2),
                    failure_code="EXPECTED_CLAIMED_OWNER_TEST",
                )
            )
    engine.dispose()


@pytest.mark.integration
def test_successful_owner_effect_reuse_across_restart_is_idempotent() -> None:
    """Completed evidence is reusable without a duplicate durable effect."""
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    scheduled_for = datetime(2026, 10, 9, 16, 0, tzinfo=UTC)
    strategy_id, version_id = uuid4(), uuid4()
    first = create_scheduled_job_run(
        f"paper:USA:{version_id}:successful-owner-first", scheduled_for,
    )
    second = create_scheduled_job_run(
        f"paper:USA:{version_id}:successful-owner-second",
        scheduled_for + timedelta(minutes=1),
    )
    effect = create_paper_effect(
        first, PaperEffectType.SIGNAL, uuid4(), "a" * 64,
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text("INSERT INTO strategies(strategy_id, name, family) "
                 "VALUES (:id, :name, 'TEST')"),
            {"id": strategy_id, "name": f"SUCCESS_OWNER_{strategy_id}"},
        )
        connection.execute(
            text("INSERT INTO strategy_versions("
                 "strategy_version_id, strategy_id, version, code_commit"
                 ") VALUES (:id, :strategy_id, 'v1', 'successful-owner-recovery')"),
            {"id": version_id, "strategy_id": strategy_id},
        )
        jobs = SqlAlchemyJobRunRepository(connection)
        assert jobs.claim(first)
        assert SqlAlchemyPaperEffectRepository(connection).record(effect)
        assert jobs.complete(
            create_job_run_completion(
                first, JobRunStatus.SUCCEEDED,
                scheduled_for + timedelta(seconds=30),
            )
        )
        assert jobs.claim(second)

    with engine.begin() as connection:
        effects = SqlAlchemyPaperEffectRepository(connection)
        assert effects.get_recoverable(effect.effect_type, effect.entity_id) == effect
        assert effects.get_reusable_for_job(
            effect.effect_type, effect.entity_id, second.job_run_id,
        ) == effect
        assert effects.record(effect) is False
        assert connection.execute(
            text("SELECT count(*) FROM paper_effects WHERE effect_id=:id"),
            {"id": effect.effect_id},
        ).scalar_one() == 1
        assert SqlAlchemyJobRunRepository(connection).complete(
            create_job_run_completion(
                second, JobRunStatus.FAILED,
                scheduled_for + timedelta(minutes=2),
                failure_code="EXPECTED_SUCCESS_OWNER_REUSE_TEST",
            )
        )
    engine.dispose()
