import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
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
from hope.application.paper import (
    PaperCycleContext,
    PaperEffectType,
    PaperOrderWriter,
    PaperRiskWriter,
    PaperSignalWriter,
    create_paper_effect,
)
from hope.application.paper.fills import PaperFillWriter
from hope.application.paper.terminals import PaperTerminalWriter
from hope.domain.execution.models import Environment, ExecutionCancellation, ExecutionRejection, Order, OrderSide
from hope.domain.execution import Fill
from hope.domain.signal.models import Signal, SignalType
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository
from hope.infrastructure.repositories.paper_fills import SqlAlchemyPaperFillRepository
from hope.infrastructure.repositories.paper_orders import SqlAlchemyPaperOrderRepository
from hope.infrastructure.repositories.paper_risk import SqlAlchemyPaperRiskRepository
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository
from hope.infrastructure.repositories.paper_terminals import SqlAlchemyPaperTerminalRepository
from hope.infrastructure.scheduling.paper_reconciliation import reconcile_completed_paper_run

UTC = timezone.utc
INPUTS_HASH = "f" * 64


def _setup_order(connection, run, instrument_id):
    context = PaperCycleContext(run)
    decision = datetime(2026, 9, 9, 20, 0, tzinfo=UTC)
    signal_id = context.signal_id(
        instrument_id=instrument_id,
        strategy_version="paper-terminal-v1",
        decision_time=decision,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"),
        inputs_hash=INPUTS_HASH,
    )
    signal = Signal(
        signal_id=signal_id,
        instrument_id=instrument_id,
        strategy_version="paper-terminal-v1",
        decision_time=decision,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"),
        inputs_hash=INPUTS_HASH,
    )
    order = Order(
        order_id=context.order_id(signal_id),
        signal_id=signal_id,
        instrument_id=instrument_id,
        side=OrderSide.BUY,
        quantity=Decimal("2"),
        environment=Environment.PAPER,
        signal_type=SignalType.ENTRY,
    )
    connection.execute(
        text(
            "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
            "VALUES (:id, :symbol, 'TEST', 'ACTIVE')"
        ),
        {"id": instrument_id, "symbol": f"TERM-{str(instrument_id)[:8]}"},
    )
    assert SqlAlchemyJobRunRepository(connection).claim(run)
    assert PaperSignalWriter(SqlAlchemyPaperSignalRepository(connection)).record(context, signal)
    assert PaperOrderWriter(SqlAlchemyPaperOrderRepository(connection)).record(context, order)
    return context, signal, order


@pytest.mark.integration
@pytest.mark.parametrize("kind", ["CANCELLED", "REJECTED"])
def test_paper_terminal_outcome_is_durable_idempotent_and_restart_readable(kind):
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    run = create_scheduled_job_run(
        f"paper-terminal-{kind.lower()}",
        datetime(2026, 9, 9, 20, 5, tzinfo=UTC),
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        context, signal, order = _setup_order(connection, run, instrument_id)
        if kind == "CANCELLED":
            outcome = ExecutionCancellation(
                order.order_id, signal.signal_id, instrument_id, Environment.PAPER,
                "TEST_CANCEL", datetime(2026, 9, 9, 20, 1, tzinfo=UTC), Decimal("2"),
            )
        else:
            outcome = ExecutionRejection(
                order.order_id, signal.signal_id, instrument_id, Environment.PAPER,
                "TEST_REJECT", datetime(2026, 9, 9, 20, 1, tzinfo=UTC),
            )
        writer = PaperTerminalWriter(SqlAlchemyPaperTerminalRepository(connection))
        assert writer.record(context, outcome)
        assert writer.record(context, outcome) is False
        assert connection.execute(
            text("SELECT count(*) FROM paper_order_terminal_events WHERE order_id=:id"),
            {"id": order.order_id},
        ).scalar_one() == 1

    # A fresh transaction/repository must reconstruct the same terminal truth.
    with engine.begin() as connection:
        restored = SqlAlchemyPaperTerminalRepository(connection).get(order.order_id)
        assert restored == outcome
        effect_type = connection.execute(
            text("SELECT effect_type FROM paper_effects WHERE entity_id=:id AND effect_type IN ('CANCELLATION', 'REJECTION')"),
            {"id": order.order_id},
        ).scalar_one()
        assert effect_type == kind.replace("CANCELLED", "CANCELLATION").replace("REJECTED", "REJECTION")
    engine.dispose()


@pytest.mark.integration
def test_paper_terminal_requires_reusable_source_order_ownership():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    source_run = create_scheduled_job_run(
        "paper-terminal-source-owner",
        datetime(2026, 9, 9, 20, 5, tzinfo=UTC),
    )
    terminal_run = create_scheduled_job_run(
        "paper-terminal-foreign-consumer",
        datetime(2026, 9, 9, 20, 6, tzinfo=UTC),
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        source_context, signal, order = _setup_order(
            connection, source_run, instrument_id
        )
        terminal_context = PaperCycleContext(terminal_run)
        jobs = SqlAlchemyJobRunRepository(connection)
        assert jobs.claim(terminal_run)
        outcome = ExecutionRejection(
            order.order_id,
            signal.signal_id,
            instrument_id,
            Environment.PAPER,
            "TEST_REJECT",
            datetime(2026, 9, 9, 20, 1, tzinfo=UTC),
        )
        writer = PaperTerminalWriter(SqlAlchemyPaperTerminalRepository(connection))

        with pytest.raises(ValueError, match="PAPER_EFFECT_IDENTITY_CONFLICT"):
            writer.record(terminal_context, outcome)

        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_order_terminal_events "
                "WHERE order_id=:id"
            ),
            {"id": order.order_id},
        ).scalar_one() == 0
        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_effects "
                "WHERE entity_id=:id AND effect_type='REJECTION'"
            ),
            {"id": order.order_id},
        ).scalar_one() == 0

        assert jobs.complete(
            create_job_run_completion(
                source_run,
                JobRunStatus.SUCCEEDED,
                source_run.scheduled_for + timedelta(seconds=30),
            )
        )
        assert writer.record(terminal_context, outcome)

    engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("mismatch", ["signal", "instrument"])
def test_paper_terminal_rejects_source_order_identity_mismatch(mismatch):
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    run = create_scheduled_job_run(
        f"paper-terminal-{mismatch}-mismatch",
        datetime(2026, 9, 9, 20, 5, tzinfo=UTC),
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        context, signal, order = _setup_order(connection, run, instrument_id)
        outcome = ExecutionRejection(
            order.order_id,
            uuid4() if mismatch == "signal" else signal.signal_id,
            uuid4() if mismatch == "instrument" else instrument_id,
            Environment.PAPER,
            "TEST_REJECT",
            datetime(2026, 9, 9, 20, 1, tzinfo=UTC),
        )

        with pytest.raises(
            ValueError,
            match="PAPER_TERMINAL_ORDER_IDENTITY_MISMATCH",
        ):
            PaperTerminalWriter(
                SqlAlchemyPaperTerminalRepository(connection)
            ).record(context, outcome)

        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_order_terminal_events "
                "WHERE order_id=:id"
            ),
            {"id": order.order_id},
        ).scalar_one() == 0
        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_effects "
                "WHERE entity_id=:id AND effect_type='REJECTION'"
            ),
            {"id": order.order_id},
        ).scalar_one() == 0

    engine.dispose()


@pytest.mark.integration
def test_paper_order_environment_is_immutable_before_terminalization():
    """Durable order history prevents PAPER/LIVE environment rewriting at the source."""
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    run = create_scheduled_job_run("paper-terminal-environment-immutable", datetime(2026, 9, 9, 20, 5, tzinfo=UTC))
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        _, _, order = _setup_order(connection, run, instrument_id)
        with pytest.raises(IntegrityError, match="ORDER_IMMUTABLE"):
            with connection.begin_nested():
                connection.execute(text("UPDATE orders SET environment='LIVE' WHERE order_id=:id"), {"id": order.order_id})
        assert connection.execute(text("SELECT environment FROM orders WHERE order_id=:id"), {"id": order.order_id}).scalar_one() == Environment.PAPER.value
    engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("kind", ["CANCELLED", "REJECTED"])
def test_complete_non_fill_lineage_reconciles_without_replaying_effects(kind):
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    strategy_id, strategy_version_id = uuid4(), uuid4()
    run = create_scheduled_job_run(
        f"paper:USA:{strategy_version_id}:terminal-reconcile-{kind.lower()}",
        datetime(2026, 9, 9, 20, 5, tzinfo=UTC),
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text("INSERT INTO strategies(strategy_id, name, family) VALUES (:id, :name, 'TEST')"),
            {"id": strategy_id, "name": f"PAPER_TERMINAL_{strategy_id}"},
        )
        connection.execute(
            text(
                "INSERT INTO strategy_versions(strategy_version_id, strategy_id, version, code_commit) "
                "VALUES (:version_id, :strategy_id, 'v1', 'paper-terminal-reconcile-test')"
            ),
            {"version_id": strategy_version_id, "strategy_id": strategy_id},
        )
        context, signal, order = _setup_order(connection, run, instrument_id)
        assert PaperRiskWriter(SqlAlchemyPaperRiskRepository(connection)).record(
            context,
            RiskAssessment(
                signal_id=signal.signal_id,
                decision=RiskDecision.APPROVE,
                reason_code="TEST_APPROVED",
                approved_quantity=Decimal("2"),
            ),
        )
        if kind == "CANCELLED":
            outcome = ExecutionCancellation(
                order.order_id,
                signal.signal_id,
                instrument_id,
                Environment.PAPER,
                "TEST_CANCEL",
                datetime(2026, 9, 9, 20, 1, tzinfo=UTC),
                Decimal("2"),
            )
        else:
            outcome = ExecutionRejection(
                order.order_id,
                signal.signal_id,
                instrument_id,
                Environment.PAPER,
                "TEST_REJECT",
                datetime(2026, 9, 9, 20, 1, tzinfo=UTC),
            )
        assert PaperTerminalWriter(
            SqlAlchemyPaperTerminalRepository(connection)
        ).record(context, outcome)
        effect_count = connection.execute(
            text("SELECT count(*) FROM paper_effects WHERE job_run_id=:id"),
            {"id": run.job_run_id},
        ).scalar_one()

        assert reconcile_completed_paper_run(
            connection,
            run,
            current=datetime.now(tz=UTC),
        ) is True
        record = SqlAlchemyJobRunRepository(connection).get_record(run.job_run_id)
        assert record is not None
        assert record.status is JobRunStatus.SUCCEEDED
        assert connection.execute(
            text("SELECT count(*) FROM paper_effects WHERE job_run_id=:id"),
            {"id": run.job_run_id},
        ).scalar_one() == effect_count

    engine.dispose()


@pytest.mark.integration
def test_paper_terminal_restart_read_requires_matching_effect_lineage():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    run = create_scheduled_job_run(
        "paper-terminal-effect-lineage",
        datetime(2026, 9, 9, 20, 5, tzinfo=UTC),
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        context, signal, order = _setup_order(connection, run, instrument_id)
        outcome = ExecutionRejection(
            order.order_id,
            signal.signal_id,
            instrument_id,
            Environment.PAPER,
            "TEST_REJECT",
            datetime(2026, 9, 9, 20, 1, tzinfo=UTC),
        )
        connection.execute(
            text(
                "INSERT INTO paper_order_terminal_events("
                "order_id, outcome, reason_code, event_time"
                ") VALUES (:order_id, 'REJECTED', :reason_code, :event_time)"
            ),
            {
                "order_id": outcome.order_id,
                "reason_code": outcome.reason_code,
                "event_time": outcome.rejection_time,
            },
        )
        repository = SqlAlchemyPaperTerminalRepository(connection)

        with pytest.raises(RuntimeError, match="PAPER_TERMINAL_EVENT_WITHOUT_EFFECT"):
            repository.get(order.order_id)

        mismatched_effect = create_paper_effect(
            context.job_run,
            PaperEffectType.REJECTION,
            order.order_id,
            "0" * 64,
        )
        assert SqlAlchemyPaperEffectRepository(connection).record(mismatched_effect)
        with pytest.raises(ValueError, match="PAPER_TERMINAL_EFFECT_PAYLOAD_CONFLICT"):
            repository.get(order.order_id)
    engine.dispose()


@pytest.mark.integration
def test_paper_terminal_history_is_immutable():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    run = create_scheduled_job_run("paper-terminal-immutable", datetime(2026, 9, 9, 20, 5, tzinfo=UTC))

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        context, signal, order = _setup_order(connection, run, instrument_id)
        outcome = ExecutionRejection(
            order.order_id, signal.signal_id, instrument_id, Environment.PAPER,
            "TEST_REJECT", datetime(2026, 9, 9, 20, 1, tzinfo=UTC),
        )
        assert PaperTerminalWriter(SqlAlchemyPaperTerminalRepository(connection)).record(context, outcome)
        with pytest.raises(IntegrityError, match="PAPER_ORDER_TERMINAL_IMMUTABLE"):
            with connection.begin_nested():
                connection.execute(
                    text("UPDATE paper_order_terminal_events SET reason_code='FORGED' WHERE order_id=:id"),
                    {"id": order.order_id},
                )
        with pytest.raises(IntegrityError, match="PAPER_ORDER_TERMINAL_IMMUTABLE"):
            with connection.begin_nested():
                connection.execute(
                    text("DELETE FROM paper_order_terminal_events WHERE order_id=:id"),
                    {"id": order.order_id},
                )
    engine.dispose()


@pytest.mark.integration
def test_paper_rejection_after_fill_fails_closed_without_terminal_effect():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    run = create_scheduled_job_run("paper-terminal-filled-reject", datetime(2026, 9, 9, 20, 5, tzinfo=UTC))

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        context, signal, order = _setup_order(connection, run, instrument_id)
        fill = Fill(
            context.fill_id(signal.signal_id, 0), order.order_id, signal.signal_id,
            instrument_id, OrderSide.BUY, Decimal("1"), Decimal("100"),
            Decimal("0.10"), Decimal("0.05"), "cost-v1",
            datetime(2026, 9, 9, 20, 1, tzinfo=UTC),
        )
        assert PaperFillWriter(SqlAlchemyPaperFillRepository(connection)).record(context, fill, sequence=0)
        outcome = ExecutionRejection(
            order.order_id, signal.signal_id, instrument_id, Environment.PAPER,
            "LATE_REJECT", datetime(2026, 9, 9, 20, 2, tzinfo=UTC),
        )
        with pytest.raises(IntegrityError, match="PAPER_REJECTION_REQUIRES_UNFILLED_ORDER"):
            PaperTerminalWriter(SqlAlchemyPaperTerminalRepository(connection)).record(context, outcome)
        assert connection.execute(
            text("SELECT count(*) FROM paper_order_terminal_events WHERE order_id=:id"),
            {"id": order.order_id},
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT count(*) FROM paper_effects WHERE entity_id=:id AND effect_type='REJECTION'"),
            {"id": order.order_id},
        ).scalar_one() == 0
    engine.dispose()


@pytest.mark.integration
def test_paper_terminal_event_cannot_precede_decision_or_latest_fill():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    run = create_scheduled_job_run(
        "paper-terminal-chronology",
        datetime(2026, 9, 9, 20, 5, tzinfo=UTC),
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        context, signal, order = _setup_order(connection, run, instrument_id)
        writer = PaperTerminalWriter(SqlAlchemyPaperTerminalRepository(connection))

        before_decision = ExecutionCancellation(
            order.order_id, signal.signal_id, instrument_id, Environment.PAPER,
            "EARLY_CANCEL", datetime(2026, 9, 9, 19, 59, tzinfo=UTC), Decimal("2"),
        )
        with pytest.raises(IntegrityError, match="PAPER_TERMINAL_PRECEDES_DECISION_TIME"):
            writer.record(context, before_decision)

        fill = Fill(
            context.fill_id(signal.signal_id, 0), order.order_id, signal.signal_id,
            instrument_id, OrderSide.BUY, Decimal("1"), Decimal("100"),
            Decimal("0.10"), Decimal("0.05"), "cost-v1",
            datetime(2026, 9, 9, 20, 2, tzinfo=UTC),
        )
        assert PaperFillWriter(
            SqlAlchemyPaperFillRepository(connection)
        ).record(context, fill, sequence=0)

        before_latest_fill = ExecutionCancellation(
            order.order_id, signal.signal_id, instrument_id, Environment.PAPER,
            "STALE_CANCEL", datetime(2026, 9, 9, 20, 1, tzinfo=UTC), Decimal("1"),
        )
        with pytest.raises(IntegrityError, match="PAPER_TERMINAL_PRECEDES_LATEST_FILL"):
            writer.record(context, before_latest_fill)

        assert connection.execute(
            text("SELECT count(*) FROM paper_order_terminal_events WHERE order_id=:id"),
            {"id": order.order_id},
        ).scalar_one() == 0
        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_effects "
                "WHERE entity_id=:id AND effect_type='CANCELLATION'"
            ),
            {"id": order.order_id},
        ).scalar_one() == 0
    engine.dispose()


@pytest.mark.integration
def test_paper_fill_after_terminal_fails_closed_without_fill_effect():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    run = create_scheduled_job_run(
        "paper-fill-after-terminal",
        datetime(2026, 9, 9, 20, 5, tzinfo=UTC),
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        context, signal, order = _setup_order(connection, run, instrument_id)
        terminal = ExecutionRejection(
            order.order_id, signal.signal_id, instrument_id, Environment.PAPER,
            "TEST_REJECT", datetime(2026, 9, 9, 20, 1, tzinfo=UTC),
        )
        assert PaperTerminalWriter(
            SqlAlchemyPaperTerminalRepository(connection)
        ).record(context, terminal)

        fill = Fill(
            context.fill_id(signal.signal_id, 0), order.order_id, signal.signal_id,
            instrument_id, OrderSide.BUY, Decimal("1"), Decimal("100"),
            Decimal("0.10"), Decimal("0.05"), "cost-v1",
            datetime(2026, 9, 9, 20, 2, tzinfo=UTC),
        )
        with pytest.raises(IntegrityError, match="PAPER_FILL_AFTER_TERMINAL_FORBIDDEN"):
            PaperFillWriter(
                SqlAlchemyPaperFillRepository(connection)
            ).record(context, fill, sequence=0)

        assert connection.execute(
            text("SELECT count(*) FROM fills WHERE fill_id=:id"),
            {"id": fill.fill_id},
        ).scalar_one() == 0
        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_effects "
                "WHERE entity_id=:id AND effect_type='FILL'"
            ),
            {"id": fill.fill_id},
        ).scalar_one() == 0
    engine.dispose()


@pytest.mark.integration
def test_paper_fill_and_terminal_writes_serialize_on_source_order():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    run = create_scheduled_job_run(
        "paper-fill-terminal-serialization",
        datetime(2026, 9, 9, 20, 5, tzinfo=UTC),
    )

    with engine.begin() as setup_connection:
        apply_migrations(setup_connection, migrations_dir)
        context, signal, order = _setup_order(
            setup_connection, run, instrument_id
        )

    terminal_connection = engine.connect()
    fill_connection = engine.connect()
    terminal_transaction = terminal_connection.begin()
    try:
        terminal = ExecutionRejection(
            order.order_id, signal.signal_id, instrument_id, Environment.PAPER,
            "TEST_REJECT", datetime(2026, 9, 9, 20, 1, tzinfo=UTC),
        )
        assert PaperTerminalWriter(
            SqlAlchemyPaperTerminalRepository(terminal_connection)
        ).record(context, terminal)

        fill = Fill(
            context.fill_id(signal.signal_id, 0), order.order_id, signal.signal_id,
            instrument_id, OrderSide.BUY, Decimal("1"), Decimal("100"),
            Decimal("0.10"), Decimal("0.05"), "cost-v1",
            datetime(2026, 9, 9, 20, 2, tzinfo=UTC),
        )
        fill_transaction = fill_connection.begin()
        try:
            fill_connection.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(OperationalError, match="lock timeout"):
                PaperFillWriter(
                    SqlAlchemyPaperFillRepository(fill_connection)
                ).record(context, fill, sequence=0)
        finally:
            fill_transaction.rollback()

        terminal_transaction.commit()

        with fill_connection.begin():
            with pytest.raises(
                IntegrityError,
                match="PAPER_FILL_AFTER_TERMINAL_FORBIDDEN",
            ):
                PaperFillWriter(
                    SqlAlchemyPaperFillRepository(fill_connection)
                ).record(context, fill, sequence=0)
            assert fill_connection.execute(
                text("SELECT count(*) FROM fills WHERE fill_id=:id"),
                {"id": fill.fill_id},
            ).scalar_one() == 0
            assert fill_connection.execute(
                text(
                    "SELECT count(*) FROM paper_effects "
                    "WHERE entity_id=:id AND effect_type='FILL'"
                ),
                {"id": fill.fill_id},
            ).scalar_one() == 0
    finally:
        if terminal_transaction.is_active:
            terminal_transaction.rollback()
        terminal_connection.close()
        fill_connection.close()
        engine.dispose()


@pytest.mark.integration
def test_paper_cancellation_after_partial_fill_requires_exact_remaining_quantity():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    run = create_scheduled_job_run(
        "paper-terminal-partial-cancel",
        datetime(2026, 9, 9, 20, 5, tzinfo=UTC),
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        context, signal, order = _setup_order(connection, run, instrument_id)
        fill = Fill(
            context.fill_id(signal.signal_id, 0), order.order_id, signal.signal_id,
            instrument_id, OrderSide.BUY, Decimal("1"), Decimal("100"),
            Decimal("0.10"), Decimal("0.05"), "cost-v1",
            datetime(2026, 9, 9, 20, 1, tzinfo=UTC),
        )
        assert PaperFillWriter(
            SqlAlchemyPaperFillRepository(connection)
        ).record(context, fill, sequence=0)

        invalid = ExecutionCancellation(
            order.order_id, signal.signal_id, instrument_id, Environment.PAPER,
            "PARTIAL_CANCEL", datetime(2026, 9, 9, 20, 2, tzinfo=UTC), Decimal("2"),
        )
        with pytest.raises(IntegrityError, match="PAPER_CANCELLATION_QUANTITY_MISMATCH"):
            PaperTerminalWriter(
                SqlAlchemyPaperTerminalRepository(connection)
            ).record(context, invalid)

        assert connection.execute(
            text("SELECT count(*) FROM paper_order_terminal_events WHERE order_id=:id"),
            {"id": order.order_id},
        ).scalar_one() == 0
        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_effects "
                "WHERE entity_id=:id AND effect_type='CANCELLATION'"
            ),
            {"id": order.order_id},
        ).scalar_one() == 0

        valid = ExecutionCancellation(
            order.order_id, signal.signal_id, instrument_id, Environment.PAPER,
            "PARTIAL_CANCEL", datetime(2026, 9, 9, 20, 2, tzinfo=UTC), Decimal("1"),
        )
        assert PaperTerminalWriter(
            SqlAlchemyPaperTerminalRepository(connection)
        ).record(context, valid)
    engine.dispose()


@pytest.mark.integration
def test_paper_terminal_restart_read_requires_source_order_lineage(monkeypatch):
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    run = create_scheduled_job_run(
        "paper-terminal-recovery-source-lineage",
        datetime(2026, 9, 9, 20, 5, tzinfo=UTC),
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        context, signal, order = _setup_order(connection, run, instrument_id)
        outcome = ExecutionRejection(
            order.order_id, signal.signal_id, instrument_id, Environment.PAPER,
            "TEST_REJECT", datetime(2026, 9, 9, 20, 1, tzinfo=UTC),
        )
        writer = PaperTerminalWriter(SqlAlchemyPaperTerminalRepository(connection))
        assert writer.record(context, outcome)
        repository = SqlAlchemyPaperTerminalRepository(connection)
        monkeypatch.setattr(
            repository._effects,
            "get_reusable_for_job",
            lambda effect_type, entity_id, job_run_id: None,
        )
        with pytest.raises(ValueError, match="PAPER_TERMINAL_SOURCE_ORDER_LINEAGE_CONFLICT"):
            repository.get(order.order_id)

    engine.dispose()
