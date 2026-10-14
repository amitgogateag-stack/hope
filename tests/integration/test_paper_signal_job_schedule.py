import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper.context import PaperCycleContext
from hope.application.paper.signals import PaperSignalWriter
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository


UTC = timezone.utc


@pytest.mark.integration
def test_postgres_rejects_future_signal_through_direct_repository_boundary() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    job_run = create_scheduled_job_run(
        f"signal-schedule-database-{uuid4()}",
        datetime(2026, 10, 14, 15, 45, tzinfo=UTC),
    )
    instrument_id = uuid4()
    future_decision = job_run.scheduled_for + timedelta(microseconds=1)
    context = PaperCycleContext(job_run)
    signal = Signal(
        signal_id=context.signal_id(
            instrument_id=instrument_id,
            strategy_version="database-schedule-v1",
            decision_time=future_decision,
            signal_type=SignalType.ENTRY,
            conviction=Decimal("0.75"),
            inputs_hash="b" * 64,
        ),
        instrument_id=instrument_id,
        strategy_version="database-schedule-v1",
        decision_time=future_decision,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.75"),
        inputs_hash="b" * 64,
    )

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            apply_migrations(connection, migrations_dir)
            connection.execute(
                text(
                    "INSERT INTO instruments("
                    "instrument_id, canonical_symbol, exchange, status"
                    ") VALUES (:instrument_id, :symbol, 'TEST', 'ACTIVE')"
                ),
                {
                    "instrument_id": instrument_id,
                    "symbol": f"SCHEDULE-{str(instrument_id)[:8]}",
                },
            )
            assert SqlAlchemyJobRunRepository(connection).claim(job_run) is True
            writer = PaperSignalWriter(
                SqlAlchemyPaperSignalRepository(connection)
            )

            with pytest.raises(
                IntegrityError,
                match="PAPER_SIGNAL_AFTER_JOB_SCHEDULE",
            ):
                writer.record(context, signal)
                connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        finally:
            transaction.rollback()

    engine.dispose()
