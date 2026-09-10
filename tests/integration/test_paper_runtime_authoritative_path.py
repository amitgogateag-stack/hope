import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from hope.application.jobs import JobRunStatus, create_scheduled_job_run
from hope.application.paper import PaperCycleContext, PaperCycleOutcome
from hope.application.paper.jobs import PaperSignalPersistenceJob
from hope.domain.execution import Environment, Fill, Order, OrderSide
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.paper_runtime import PaperJobDefinition, PaperJobRegistry, run_paper_once
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository

UTC = timezone.utc
INPUTS_HASH = "f" * 64


@pytest.mark.integration
def test_authoritative_paper_runtime_persists_signal_order_and_fill_in_separate_runs() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    portfolio_id = uuid4()
    signal_run = create_scheduled_job_run(
        "paper-runtime-signal",
        datetime(2026, 9, 10, 6, 0, tzinfo=UTC),
    )
    order_run = create_scheduled_job_run(
        "paper-runtime-order",
        datetime(2026, 9, 10, 6, 1, tzinfo=UTC),
    )
    fill_run = create_scheduled_job_run(
        "paper-runtime-fill",
        datetime(2026, 9, 10, 6, 2, tzinfo=UTC),
    )

    signal_context = PaperCycleContext(signal_run)
    order_context = PaperCycleContext(order_run)
    fill_context = PaperCycleContext(fill_run)
    decision_time = datetime(2026, 9, 10, 5, 59, tzinfo=UTC)
    signal_id = signal_context.signal_id(
        instrument_id=instrument_id,
        strategy_version="paper-runtime-v1",
        decision_time=decision_time,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"),
        inputs_hash=INPUTS_HASH,
    )
    signal = Signal(
        signal_id=signal_id,
        instrument_id=instrument_id,
        strategy_version="paper-runtime-v1",
        decision_time=decision_time,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"),
        inputs_hash=INPUTS_HASH,
    )
    order = Order(
        order_id=order_context.order_id(signal_id),
        signal_id=signal_id,
        instrument_id=instrument_id,
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        environment=Environment.PAPER,
        signal_type=SignalType.ENTRY,
    )
    fill = Fill(
        fill_context.fill_id(signal_id, 0),
        order.order_id,
        signal_id,
        instrument_id,
        OrderSide.BUY,
        Decimal("1"),
        Decimal("100"),
        Decimal("0.25"),
        Decimal("0"),
        "paper-runtime-cost-v1",
        datetime(2026, 9, 10, 6, 2, tzinfo=UTC),
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text(
                "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                "VALUES (:id, 'PAPER-RUNTIME', 'TEST', 'ACTIVE')"
            ),
            {"id": instrument_id},
        )

    registry = PaperJobRegistry(
        [
            PaperJobDefinition(signal_run.job_key, PaperSignalPersistenceJob(signal)),
            PaperJobDefinition(order_run.job_key, lambda context: context.record_order(order)),
            PaperJobDefinition(
                fill_run.job_key,
                lambda context: context.record_fill(
                    portfolio_id,
                    Decimal("1000"),
                    fill,
                    sequence=0,
                ),
            ),
        ]
    )
    now = lambda: fill_run.scheduled_for + timedelta(minutes=1)

    assert run_paper_once(engine, signal_run, registry, now=now) is PaperCycleOutcome.EXECUTED
    assert run_paper_once(engine, order_run, registry, now=now) is PaperCycleOutcome.EXECUTED
    assert run_paper_once(engine, fill_run, registry, now=now) is PaperCycleOutcome.EXECUTED

    with engine.connect() as connection:
        jobs = SqlAlchemyJobRunRepository(connection)
        assert connection.execute(
            text("SELECT count(*) FROM signals WHERE signal_id=:id"),
            {"id": signal.signal_id},
        ).scalar_one() == 1
        assert connection.execute(
            text("SELECT count(*) FROM orders WHERE order_id=:id"),
            {"id": order.order_id},
        ).scalar_one() == 1
        assert connection.execute(
            text("SELECT count(*) FROM fills WHERE fill_id=:id"),
            {"id": fill.fill_id},
        ).scalar_one() == 1
        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_portfolio_fill_applications "
                "WHERE portfolio_id=:portfolio_id AND fill_id=:fill_id"
            ),
            {"portfolio_id": portfolio_id, "fill_id": fill.fill_id},
        ).scalar_one() == 1

        effect_jobs = dict(
            connection.execute(
                text(
                    "SELECT effect_type, job_run_id FROM paper_effects "
                    "WHERE entity_id IN (:signal_id, :order_id, :fill_id)"
                ),
                {
                    "signal_id": signal.signal_id,
                    "order_id": order.order_id,
                    "fill_id": fill.fill_id,
                },
            ).all()
        )
        assert effect_jobs["SIGNAL"] == signal_run.job_run_id
        assert effect_jobs["ORDER"] == order_run.job_run_id
        assert effect_jobs["FILL"] == fill_run.job_run_id

        for run in (signal_run, order_run, fill_run):
            record = jobs.get_record(run.job_run_id)
            assert record is not None
            assert record.status is JobRunStatus.SUCCEEDED
