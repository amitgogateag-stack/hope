import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.application.jobs import JobRunStatus, create_job_run_completion, create_scheduled_job_run
from hope.application.paper import PaperCycleContext, PaperOrderWriter, PaperSignalWriter
from hope.domain.execution.models import Environment, Order, OrderSide
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_orders import SqlAlchemyPaperOrderRepository
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository


UTC = timezone.utc
INPUTS_HASH = "e" * 64


def make_signal(context: PaperCycleContext, instrument_id, decision_time: datetime) -> Signal:
    signal_id = context.signal_id(
        instrument_id=instrument_id,
        strategy_version="paper-order-v1",
        decision_time=decision_time,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.7"),
        inputs_hash=INPUTS_HASH,
    )
    return Signal(
        signal_id=signal_id,
        instrument_id=instrument_id,
        strategy_version="paper-order-v1",
        decision_time=decision_time,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.7"),
        inputs_hash=INPUTS_HASH,
    )


def make_order(context: PaperCycleContext, signal: Signal, instrument_id=None) -> Order:
    return Order(
        order_id=context.order_id(signal.signal_id),
        signal_id=signal.signal_id,
        instrument_id=instrument_id or signal.instrument_id,
        side=OrderSide.BUY,
        quantity=Decimal("3"),
        environment=Environment.PAPER,
        signal_type=signal.signal_type,
    )


@pytest.mark.integration
def test_paper_order_is_durable_and_idempotent_across_scheduled_cycles() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    signal_run = create_scheduled_job_run(
        "paper-order-source-signal",
        datetime(2026, 9, 9, 23, 10, tzinfo=UTC),
    )
    first_order_run = create_scheduled_job_run(
        "paper-order-first",
        datetime(2026, 9, 9, 23, 11, tzinfo=UTC),
    )
    second_order_run = create_scheduled_job_run(
        "paper-order-second",
        datetime(2026, 9, 9, 23, 12, tzinfo=UTC),
    )
    signal_context = PaperCycleContext(signal_run)
    first_order_context = PaperCycleContext(first_order_run)
    second_order_context = PaperCycleContext(second_order_run)
    signal = make_signal(
        signal_context,
        instrument_id,
        datetime(2026, 9, 9, 23, 9, tzinfo=UTC),
    )
    order = make_order(first_order_context, signal)
    assert second_order_context.order_id(signal.signal_id) == order.order_id

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text(
                "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                "VALUES (:instrument_id, 'PAPER-ORDER', 'TEST', 'ACTIVE')"
            ),
            {"instrument_id": instrument_id},
        )

        jobs = SqlAlchemyJobRunRepository(connection)
        signal_writer = PaperSignalWriter(SqlAlchemyPaperSignalRepository(connection))
        order_writer = PaperOrderWriter(SqlAlchemyPaperOrderRepository(connection))

        assert jobs.claim(signal_run) is True
        assert signal_writer.record(signal_context, signal) is True
        assert jobs.complete(
            create_job_run_completion(
                signal_run,
                JobRunStatus.SUCCEEDED,
                signal_run.scheduled_for + timedelta(seconds=30),
            )
        ) is True

        assert jobs.claim(first_order_run) is True
        assert order_writer.record(first_order_context, order) is True
        assert jobs.complete(
            create_job_run_completion(
                first_order_run,
                JobRunStatus.SUCCEEDED,
                first_order_run.scheduled_for + timedelta(seconds=30),
            )
        ) is True

        assert jobs.claim(second_order_run) is True
        assert order_writer.record(second_order_context, order) is False
        assert jobs.complete(
            create_job_run_completion(
                second_order_run,
                JobRunStatus.SUCCEEDED,
                second_order_run.scheduled_for + timedelta(seconds=30),
            )
        ) is True

        assert connection.execute(
            text("SELECT count(*) FROM orders WHERE order_id = :order_id"),
            {"order_id": order.order_id},
        ).scalar_one() == 1
        effect = connection.execute(
            text(
                "SELECT job_run_id, payload_hash FROM paper_effects "
                "WHERE effect_type = 'ORDER' AND entity_id = :order_id"
            ),
            {"order_id": order.order_id},
        ).mappings().one()
        assert effect["job_run_id"] == first_order_run.job_run_id
        assert len(effect["payload_hash"]) == 64


@pytest.mark.integration
def test_paper_order_requires_tracked_signal_and_rolls_back_failed_order_effect() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    source_instrument_id = uuid4()
    other_instrument_id = uuid4()
    signal_run = create_scheduled_job_run(
        "paper-order-mismatch-source",
        datetime(2026, 9, 9, 23, 13, tzinfo=UTC),
    )
    failed_order_run = create_scheduled_job_run(
        "paper-order-mismatch-failure",
        datetime(2026, 9, 9, 23, 14, tzinfo=UTC),
    )
    untracked_order_run = create_scheduled_job_run(
        "paper-order-untracked-signal",
        datetime(2026, 9, 9, 23, 15, tzinfo=UTC),
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text(
                "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) VALUES "
                "(:source_id, 'PAPER-ORDER-SOURCE', 'TEST', 'ACTIVE'), "
                "(:other_id, 'PAPER-ORDER-OTHER', 'TEST', 'ACTIVE')"
            ),
            {"source_id": source_instrument_id, "other_id": other_instrument_id},
        )

        jobs = SqlAlchemyJobRunRepository(connection)
        signal_writer = PaperSignalWriter(SqlAlchemyPaperSignalRepository(connection))
        order_writer = PaperOrderWriter(SqlAlchemyPaperOrderRepository(connection))

        signal_context = PaperCycleContext(signal_run)
        signal = make_signal(
            signal_context,
            source_instrument_id,
            datetime(2026, 9, 9, 23, 12, tzinfo=UTC),
        )
        assert jobs.claim(signal_run) is True
        assert signal_writer.record(signal_context, signal) is True
        assert jobs.complete(
            create_job_run_completion(
                signal_run,
                JobRunStatus.SUCCEEDED,
                signal_run.scheduled_for + timedelta(seconds=30),
            )
        ) is True

        failed_context = PaperCycleContext(failed_order_run)
        mismatched_order = make_order(failed_context, signal, instrument_id=other_instrument_id)
        assert jobs.claim(failed_order_run) is True
        with pytest.raises(IntegrityError) as exc_info:
            order_writer.record(failed_context, mismatched_order)
        assert "ORDER_SIGNAL_INSTRUMENT_MISMATCH" in str(exc_info.value)
        assert connection.execute(
            text("SELECT count(*) FROM orders WHERE order_id = :order_id"),
            {"order_id": mismatched_order.order_id},
        ).scalar_one() == 0
        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_effects "
                "WHERE effect_type = 'ORDER' AND entity_id = :order_id"
            ),
            {"order_id": mismatched_order.order_id},
        ).scalar_one() == 0
        assert jobs.complete(
            create_job_run_completion(
                failed_order_run,
                JobRunStatus.FAILED,
                failed_order_run.scheduled_for + timedelta(minutes=1),
                failure_code="IntegrityError",
            )
        ) is True

        untracked_context = PaperCycleContext(untracked_order_run)
        raw_signal_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO signals(signal_id, instrument_id, decision_time, state) "
                "VALUES (:signal_id, :instrument_id, :decision_time, 'SIGNAL')"
            ),
            {
                "signal_id": raw_signal_id,
                "instrument_id": source_instrument_id,
                "decision_time": datetime(2026, 9, 9, 23, 14, tzinfo=UTC),
            },
        )
        untracked_order = Order(
            order_id=untracked_context.order_id(raw_signal_id),
            signal_id=raw_signal_id,
            instrument_id=source_instrument_id,
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            environment=Environment.PAPER,
            signal_type=SignalType.ENTRY,
        )
        assert jobs.claim(untracked_order_run) is True
        with pytest.raises(ValueError, match="PAPER_ORDER_SOURCE_SIGNAL_UNTRACKED"):
            order_writer.record(untracked_context, untracked_order)
        assert connection.execute(
            text("SELECT count(*) FROM orders WHERE order_id = :order_id"),
            {"order_id": untracked_order.order_id},
        ).scalar_one() == 0
