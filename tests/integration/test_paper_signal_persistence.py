import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.application.jobs import JobRunStatus, create_job_run_completion, create_scheduled_job_run
from hope.application.paper import PaperCycleContext, PaperSignalWriter
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository


UTC = timezone.utc
INPUTS_HASH = "d" * 64


def make_signal(context: PaperCycleContext, instrument_id, decision_time: datetime) -> Signal:
    signal_id = context.signal_id(
        instrument_id=instrument_id,
        strategy_version="paper-signal-v1",
        decision_time=decision_time,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.6"),
        inputs_hash=INPUTS_HASH,
    )
    return Signal(
        signal_id=signal_id,
        instrument_id=instrument_id,
        strategy_version="paper-signal-v1",
        decision_time=decision_time,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.6"),
        inputs_hash=INPUTS_HASH,
    )


@pytest.mark.integration
def test_paper_signal_is_durable_and_idempotent_across_scheduled_cycles() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    first_run = create_scheduled_job_run(
        "paper-signal-first",
        datetime(2026, 9, 9, 23, 0, tzinfo=UTC),
    )
    second_run = create_scheduled_job_run(
        "paper-signal-second",
        datetime(2026, 9, 9, 23, 1, tzinfo=UTC),
    )
    first_context = PaperCycleContext(first_run)
    second_context = PaperCycleContext(second_run)
    decision_time = datetime(2026, 9, 9, 22, 59, tzinfo=UTC)
    signal = make_signal(first_context, instrument_id, decision_time)
    assert second_context.signal_id(
        instrument_id=instrument_id,
        strategy_version=signal.strategy_version,
        decision_time=signal.decision_time,
        signal_type=signal.signal_type,
        conviction=signal.conviction,
        inputs_hash=signal.inputs_hash,
    ) == signal.signal_id

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text(
                "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                "VALUES (:instrument_id, 'PAPER-SIGNAL', 'TEST', 'ACTIVE')"
            ),
            {"instrument_id": instrument_id},
        )

        jobs = SqlAlchemyJobRunRepository(connection)
        writer = PaperSignalWriter(SqlAlchemyPaperSignalRepository(connection))
        assert jobs.claim(first_run) is True
        assert writer.record(first_context, signal) is True
        assert jobs.complete(
            create_job_run_completion(
                first_run,
                JobRunStatus.SUCCEEDED,
                first_run.scheduled_for + timedelta(seconds=30),
            )
        ) is True

        assert jobs.claim(second_run) is True
        assert writer.record(second_context, signal) is False
        assert jobs.complete(
            create_job_run_completion(
                second_run,
                JobRunStatus.SUCCEEDED,
                second_run.scheduled_for + timedelta(seconds=30),
            )
        ) is True

        assert connection.execute(
            text("SELECT count(*) FROM signals WHERE signal_id = :signal_id"),
            {"signal_id": signal.signal_id},
        ).scalar_one() == 1
        effect = connection.execute(
            text(
                "SELECT job_run_id, payload_hash FROM paper_effects "
                "WHERE effect_type = 'SIGNAL' AND entity_id = :signal_id"
            ),
            {"signal_id": signal.signal_id},
        ).mappings().one()
        assert effect["job_run_id"] == first_run.job_run_id
        assert len(effect["payload_hash"]) == 64


@pytest.mark.integration
def test_paper_signal_persistence_rejects_untracked_row_and_rolls_back_failed_effect() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    untracked_run = create_scheduled_job_run(
        "paper-signal-untracked",
        datetime(2026, 9, 9, 23, 2, tzinfo=UTC),
    )
    failed_run = create_scheduled_job_run(
        "paper-signal-fk-failure",
        datetime(2026, 9, 9, 23, 3, tzinfo=UTC),
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text(
                "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                "VALUES (:instrument_id, 'PAPER-UNTRACKED', 'TEST', 'ACTIVE')"
            ),
            {"instrument_id": instrument_id},
        )
        jobs = SqlAlchemyJobRunRepository(connection)
        repository = SqlAlchemyPaperSignalRepository(connection)
        writer = PaperSignalWriter(repository)

        assert jobs.claim(untracked_run) is True
        untracked_context = PaperCycleContext(untracked_run)
        untracked_signal = make_signal(
            untracked_context,
            instrument_id,
            datetime(2026, 9, 9, 22, 58, tzinfo=UTC),
        )
        connection.execute(
            text(
                "INSERT INTO signals(signal_id, instrument_id, decision_time, state) "
                "VALUES (:signal_id, :instrument_id, :decision_time, 'SIGNAL')"
            ),
            {
                "signal_id": untracked_signal.signal_id,
                "instrument_id": untracked_signal.instrument_id,
                "decision_time": untracked_signal.decision_time,
            },
        )
        with pytest.raises(ValueError, match="PAPER_SIGNAL_UNTRACKED_DURABLE_STATE"):
            writer.record(untracked_context, untracked_signal)
        assert connection.execute(
            text("SELECT count(*) FROM paper_effects WHERE entity_id = :signal_id"),
            {"signal_id": untracked_signal.signal_id},
        ).scalar_one() == 0

        assert jobs.claim(failed_run) is True
        failed_context = PaperCycleContext(failed_run)
        missing_instrument_signal = make_signal(
            failed_context,
            uuid4(),
            datetime(2026, 9, 9, 22, 59, tzinfo=UTC),
        )
        with pytest.raises(IntegrityError):
            writer.record(failed_context, missing_instrument_signal)

        assert connection.execute(
            text("SELECT count(*) FROM paper_effects WHERE entity_id = :signal_id"),
            {"signal_id": missing_instrument_signal.signal_id},
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT count(*) FROM signals WHERE signal_id = :signal_id"),
            {"signal_id": missing_instrument_signal.signal_id},
        ).scalar_one() == 0

        assert jobs.complete(
            create_job_run_completion(
                failed_run,
                JobRunStatus.FAILED,
                failed_run.scheduled_for + timedelta(minutes=1),
                failure_code="IntegrityError",
            )
        ) is True
