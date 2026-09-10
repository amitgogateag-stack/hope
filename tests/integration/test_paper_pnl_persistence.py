import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from hope.application.jobs import JobRunStatus, create_job_run_completion, create_scheduled_job_run
from hope.application.paper import PaperCycleContext, PaperPnLEvent, PaperSignalWriter
from hope.application.paper.pnl import PaperPnLWriter
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_pnl import SqlAlchemyPaperPnLRepository
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository


UTC = timezone.utc
INPUTS_HASH = "e" * 64


def make_signal(context: PaperCycleContext, instrument_id, decision_time: datetime) -> Signal:
    signal_id = context.signal_id(
        instrument_id=instrument_id,
        strategy_version="paper-pnl-v1",
        decision_time=decision_time,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.7"),
        inputs_hash=INPUTS_HASH,
    )
    return Signal(
        signal_id=signal_id,
        instrument_id=instrument_id,
        strategy_version="paper-pnl-v1",
        decision_time=decision_time,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.7"),
        inputs_hash=INPUTS_HASH,
    )


@pytest.mark.integration
def test_paper_pnl_is_durable_and_idempotent_across_scheduled_cycles() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    position_id = uuid4()
    first_run = create_scheduled_job_run("paper-pnl-first", datetime(2026, 9, 10, 0, 0, tzinfo=UTC))
    second_run = create_scheduled_job_run("paper-pnl-second", datetime(2026, 9, 10, 0, 1, tzinfo=UTC))
    first_context = PaperCycleContext(first_run)
    second_context = PaperCycleContext(second_run)
    signal = make_signal(first_context, instrument_id, datetime(2026, 9, 9, 23, 59, tzinfo=UTC))
    event = PaperPnLEvent(
        first_context.pnl_event_id(position_id, 0),
        position_id,
        Decimal("25.75"),
        datetime(2026, 9, 10, 0, 0, tzinfo=UTC),
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text("INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) VALUES (:id, 'PAPER-PNL', 'TEST', 'ACTIVE')"),
            {"id": instrument_id},
        )
        jobs = SqlAlchemyJobRunRepository(connection)
        signal_writer = PaperSignalWriter(SqlAlchemyPaperSignalRepository(connection))
        pnl_writer = PaperPnLWriter(SqlAlchemyPaperPnLRepository(connection))

        assert jobs.claim(first_run) is True
        assert signal_writer.record(first_context, signal) is True
        connection.execute(
            text("INSERT INTO positions(position_id, instrument_id, opened_from_signal_id, quantity, opened_at) VALUES (:position_id, :instrument_id, :signal_id, 1, :opened_at)"),
            {
                "position_id": position_id,
                "instrument_id": instrument_id,
                "signal_id": signal.signal_id,
                "opened_at": signal.decision_time,
            },
        )
        assert pnl_writer.record(first_context, event, sequence=0) is True
        assert jobs.complete(
            create_job_run_completion(first_run, JobRunStatus.SUCCEEDED, first_run.scheduled_for + timedelta(seconds=30))
        ) is True

        assert jobs.claim(second_run) is True
        assert pnl_writer.record(second_context, event, sequence=0) is False
        assert jobs.complete(
            create_job_run_completion(second_run, JobRunStatus.SUCCEEDED, second_run.scheduled_for + timedelta(seconds=30))
        ) is True

        assert connection.execute(
            text("SELECT count(*) FROM pnl_events WHERE pnl_event_id = :id"), {"id": event.pnl_event_id}
        ).scalar_one() == 1
        effect = connection.execute(
            text("SELECT job_run_id FROM paper_effects WHERE effect_type = 'PNL' AND entity_id = :id"),
            {"id": event.pnl_event_id},
        ).scalar_one()
        assert effect == first_run.job_run_id


@pytest.mark.integration
def test_paper_pnl_rejects_missing_or_untracked_position_without_orphan_effect() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    missing_run = create_scheduled_job_run("paper-pnl-missing", datetime(2026, 9, 10, 0, 2, tzinfo=UTC))
    raw_run = create_scheduled_job_run("paper-pnl-untracked", datetime(2026, 9, 10, 0, 3, tzinfo=UTC))

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        jobs = SqlAlchemyJobRunRepository(connection)
        writer = PaperPnLWriter(SqlAlchemyPaperPnLRepository(connection))

        assert jobs.claim(missing_run) is True
        missing_context = PaperCycleContext(missing_run)
        missing_position = uuid4()
        missing_event = PaperPnLEvent(
            missing_context.pnl_event_id(missing_position, 0),
            missing_position,
            Decimal("1"),
            missing_run.scheduled_for,
        )
        with pytest.raises(ValueError, match="PAPER_PNL_POSITION_NOT_FOUND"):
            writer.record(missing_context, missing_event, sequence=0)
        assert connection.execute(
            text("SELECT count(*) FROM paper_effects WHERE entity_id = :id"), {"id": missing_event.pnl_event_id}
        ).scalar_one() == 0

        instrument_id = uuid4()
        signal_id = uuid4()
        position_id = uuid4()
        connection.execute(
            text("INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) VALUES (:id, 'PAPER-PNL-RAW', 'TEST', 'ACTIVE')"),
            {"id": instrument_id},
        )
        connection.execute(
            text("INSERT INTO signals(signal_id, instrument_id, decision_time, state) VALUES (:signal_id, :instrument_id, :decision_time, 'SIGNAL')"),
            {"signal_id": signal_id, "instrument_id": instrument_id, "decision_time": raw_run.scheduled_for - timedelta(minutes=1)},
        )
        connection.execute(
            text("INSERT INTO positions(position_id, instrument_id, opened_from_signal_id, quantity, opened_at) VALUES (:position_id, :instrument_id, :signal_id, 1, :opened_at)"),
            {"position_id": position_id, "instrument_id": instrument_id, "signal_id": signal_id, "opened_at": raw_run.scheduled_for - timedelta(minutes=1)},
        )
        assert jobs.claim(raw_run) is True
        raw_context = PaperCycleContext(raw_run)
        raw_event = PaperPnLEvent(
            raw_context.pnl_event_id(position_id, 0),
            position_id,
            Decimal("2"),
            raw_run.scheduled_for,
        )
        with pytest.raises(ValueError, match="PAPER_PNL_POSITION_UNTRACKED_SIGNAL"):
            writer.record(raw_context, raw_event, sequence=0)
        assert connection.execute(
            text("SELECT count(*) FROM paper_effects WHERE entity_id = :id"), {"id": raw_event.pnl_event_id}
        ).scalar_one() == 0
