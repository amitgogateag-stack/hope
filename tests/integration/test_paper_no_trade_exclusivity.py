import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper.context import PaperCycleContext
from hope.application.paper.no_trade import PaperNoTradeWriter
from hope.application.paper.signals import PaperSignalWriter
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_no_trade import (
    SqlAlchemyPaperNoTradeRepository,
)
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository


UTC = timezone.utc


def _setup(connection, job_key):
    scheduled_for = datetime(2026, 10, 15, 15, 0, tzinfo=UTC)
    run = create_scheduled_job_run(job_key, scheduled_for)
    connection.execute(
        text(
            "DELETE FROM job_runs "
            "WHERE job_key=:job_key AND scheduled_for=:scheduled_for"
        ),
        {"job_key": job_key, "scheduled_for": scheduled_for},
    )
    assert SqlAlchemyJobRunRepository(connection).claim(run)
    instrument_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
            "VALUES (:instrument_id, :symbol, 'TEST', 'ACTIVE')"
        ),
        {"instrument_id": instrument_id, "symbol": f"EXCL-{instrument_id}"},
    )
    context = PaperCycleContext(run)
    inputs_hash = "e" * 64
    signal = Signal(
        signal_id=context.signal_id(
            instrument_id=instrument_id,
            strategy_version="paper-exclusive-v1",
            decision_time=scheduled_for,
            signal_type=SignalType.ENTRY,
            conviction=Decimal("0.7"),
            inputs_hash=inputs_hash,
        ),
        instrument_id=instrument_id,
        strategy_version="paper-exclusive-v1",
        decision_time=scheduled_for,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.7"),
        inputs_hash=inputs_hash,
    )
    return context, signal


def _engine():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    return create_engine(url)


@pytest.mark.integration
@pytest.mark.parametrize("first_outcome", ["NO_TRADE", "SIGNAL"])
def test_no_trade_and_signal_effects_cannot_coexist_for_one_job(first_outcome):
    engine = _engine()
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        context, signal = _setup(
            connection,
            f"paper-no-trade-exclusivity-{first_outcome.lower()}",
        )
        no_trade = PaperNoTradeWriter(SqlAlchemyPaperNoTradeRepository(connection))
        signal_writer = PaperSignalWriter(SqlAlchemyPaperSignalRepository(connection))

        if first_outcome == "NO_TRADE":
            assert no_trade.record(
                context,
                strategy_version=signal.strategy_version,
                decision_time=signal.decision_time,
                inputs_hash=signal.inputs_hash,
            )
            assert no_trade.record(
                context,
                strategy_version=signal.strategy_version,
                decision_time=signal.decision_time,
                inputs_hash=signal.inputs_hash,
            ) is False
            with pytest.raises(
                IntegrityError,
                match="PAPER_NO_TRADE_EFFECT_EXCLUSIVITY",
            ):
                signal_writer.record(context, signal)
            expected_type = "NO_TRADE"
            rejected_type = "SIGNAL"
        else:
            assert signal_writer.record(context, signal)
            with pytest.raises(
                IntegrityError,
                match="PAPER_NO_TRADE_EFFECT_EXCLUSIVITY",
            ):
                no_trade.record(
                    context,
                    strategy_version=signal.strategy_version,
                    decision_time=signal.decision_time,
                    inputs_hash=signal.inputs_hash,
                )
            expected_type = "SIGNAL"
            rejected_type = "NO_TRADE"

        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_effects "
                "WHERE job_run_id=:job_run_id AND effect_type=:effect_type"
            ),
            {
                "job_run_id": context.job_run.job_run_id,
                "effect_type": expected_type,
            },
        ).scalar_one() == 1
        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_effects "
                "WHERE job_run_id=:job_run_id AND effect_type=:effect_type"
            ),
            {
                "job_run_id": context.job_run.job_run_id,
                "effect_type": rejected_type,
            },
        ).scalar_one() == 0
        if first_outcome == "NO_TRADE":
            assert connection.execute(
                text("SELECT count(*) FROM signals WHERE signal_id=:signal_id"),
                {"signal_id": signal.signal_id},
            ).scalar_one() == 0
        else:
            assert connection.execute(
                text(
                    "SELECT count(*) FROM paper_no_trade_decisions "
                    "WHERE job_run_id=:job_run_id"
                ),
                {"job_run_id": context.job_run.job_run_id},
            ).scalar_one() == 0

    engine.dispose()
