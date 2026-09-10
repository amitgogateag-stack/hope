import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from hope.application.jobs import JobRunStatus, create_job_run_completion, create_scheduled_job_run
from hope.application.paper import PaperCycleContext, PaperOrderWriter, PaperSignalWriter
from hope.application.paper.fills import PaperFillWriter
from hope.domain.execution import Environment, Fill, Order, OrderSide
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_fills import SqlAlchemyPaperFillRepository
from hope.infrastructure.repositories.paper_orders import SqlAlchemyPaperOrderRepository
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository

UTC = timezone.utc
INPUTS_HASH = "e" * 64


def make_chain(context, instrument_id):
    decision = datetime(2026, 9, 9, 23, 40, tzinfo=UTC)
    signal_id = context.signal_id(instrument_id=instrument_id, strategy_version="paper-fill-v1", decision_time=decision,
        signal_type=SignalType.ENTRY, conviction=Decimal("0.7"), inputs_hash=INPUTS_HASH)
    signal = Signal(signal_id=signal_id, instrument_id=instrument_id, strategy_version="paper-fill-v1", decision_time=decision,
        signal_type=SignalType.ENTRY, conviction=Decimal("0.7"), inputs_hash=INPUTS_HASH)
    order = Order(order_id=context.order_id(signal_id), signal_id=signal_id, instrument_id=instrument_id,
        side=OrderSide.BUY, quantity=Decimal("2"), environment=Environment.PAPER, signal_type=SignalType.ENTRY)
    fill = Fill(context.fill_id(signal_id, 0), order.order_id, signal_id, instrument_id, OrderSide.BUY,
        Decimal("2"), Decimal("101.5"), Decimal("0.25"), Decimal("0.5"), "cost-v1",
        datetime(2026, 9, 9, 23, 41, tzinfo=UTC))
    return signal, order, fill


@pytest.mark.integration
def test_paper_fill_is_durable_and_idempotent_across_scheduled_cycles():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url: pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url); migrations_dir = Path(__file__).parents[2] / "migrations"; instrument_id = uuid4()
    first = create_scheduled_job_run("paper-fill-first", datetime(2026, 9, 9, 23, 42, tzinfo=UTC))
    second = create_scheduled_job_run("paper-fill-second", datetime(2026, 9, 9, 23, 43, tzinfo=UTC))
    c1, c2 = PaperCycleContext(first), PaperCycleContext(second); signal, order, fill = make_chain(c1, instrument_id)
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(text("INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) VALUES (:id,'PAPER-FILL','TEST','ACTIVE')"), {"id": instrument_id})
        jobs = SqlAlchemyJobRunRepository(connection)
        sw = PaperSignalWriter(SqlAlchemyPaperSignalRepository(connection)); ow = PaperOrderWriter(SqlAlchemyPaperOrderRepository(connection)); fw = PaperFillWriter(SqlAlchemyPaperFillRepository(connection))
        assert jobs.claim(first); assert sw.record(c1, signal); assert ow.record(c1, order); assert fw.record(c1, fill, sequence=0)
        assert jobs.complete(create_job_run_completion(first, JobRunStatus.SUCCEEDED, first.scheduled_for + timedelta(seconds=30)))
        assert jobs.claim(second); assert fw.record(c2, fill, sequence=0) is False
        assert connection.execute(text("SELECT count(*) FROM fills WHERE fill_id=:id"), {"id": fill.fill_id}).scalar_one() == 1
        effect = connection.execute(text("SELECT job_run_id FROM paper_effects WHERE effect_type='FILL' AND entity_id=:id"), {"id": fill.fill_id}).scalar_one()
        assert effect == first.job_run_id


@pytest.mark.integration
def test_paper_fill_requires_tracked_order_and_rolls_back_failed_fill_effect():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url: pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url); migrations_dir = Path(__file__).parents[2] / "migrations"; instrument_id = uuid4()
    run = create_scheduled_job_run("paper-fill-guard", datetime(2026, 9, 9, 23, 44, tzinfo=UTC)); context = PaperCycleContext(run)
    signal, order, fill = make_chain(context, instrument_id)
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(text("INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) VALUES (:id,'PAPER-FILL-GUARD','TEST','ACTIVE')"), {"id": instrument_id})
        jobs = SqlAlchemyJobRunRepository(connection); assert jobs.claim(run)
        fw = PaperFillWriter(SqlAlchemyPaperFillRepository(connection))
        with pytest.raises(ValueError, match="PAPER_FILL_SOURCE_ORDER_UNTRACKED"):
            fw.record(context, fill, sequence=0)
        assert connection.execute(text("SELECT count(*) FROM paper_effects WHERE entity_id=:id"), {"id": fill.fill_id}).scalar_one() == 0
        PaperSignalWriter(SqlAlchemyPaperSignalRepository(connection)).record(context, signal)
        PaperOrderWriter(SqlAlchemyPaperOrderRepository(connection)).record(context, order)
        broken = Fill(fill.fill_id, uuid4(), fill.signal_id, fill.instrument_id, fill.side, fill.quantity, fill.price, fill.commission, fill.slippage, fill.cost_model_version, fill.fill_time)
        with pytest.raises(ValueError, match="PAPER_FILL_SOURCE_ORDER_UNTRACKED"):
            fw.record(context, broken, sequence=0)
        assert connection.execute(text("SELECT count(*) FROM paper_effects WHERE effect_type='FILL' AND entity_id=:id"), {"id": fill.fill_id}).scalar_one() == 0
