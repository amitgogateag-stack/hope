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
    PaperAccountingWriter,
    PaperCycleContext,
    PaperOrderWriter,
    PaperSignalWriter,
)
from hope.application.paper.effects import PaperEffectType, create_paper_effect
from hope.application.paper.fills import PaperFillWriter
from hope.application.paper.portfolio_pnl import paper_portfolio_pnl_event_id
from hope.domain.execution import Environment, Fill, Order, OrderSide
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_accounting import SqlAlchemyPaperAccountingRepository
from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository
from hope.infrastructure.repositories.paper_fills import SqlAlchemyPaperFillRepository
from hope.infrastructure.repositories.paper_orders import SqlAlchemyPaperOrderRepository
from hope.infrastructure.repositories.paper_portfolio import SqlAlchemyPaperPortfolioRepository
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository

UTC = timezone.utc
INPUTS_HASH = "c" * 64


def persist_fill(connection, context, instrument_id, *, side, price, minute):
    decision = datetime(2026, 9, 10, 2, minute, tzinfo=UTC)
    signal_id = context.signal_id(
        instrument_id=instrument_id,
        strategy_version=f"paper-atomic-accounting-{minute}",
        decision_time=decision,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.75"),
        inputs_hash=INPUTS_HASH,
    )
    signal = Signal(
        signal_id=signal_id,
        instrument_id=instrument_id,
        strategy_version=f"paper-atomic-accounting-{minute}",
        decision_time=decision,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.75"),
        inputs_hash=INPUTS_HASH,
    )
    order = Order(
        order_id=context.order_id(signal_id),
        signal_id=signal_id,
        instrument_id=instrument_id,
        side=side,
        quantity=Decimal("1"),
        environment=Environment.PAPER,
        signal_type=SignalType.ENTRY,
    )
    fill = Fill(
        context.fill_id(signal_id, 0),
        order.order_id,
        signal_id,
        instrument_id,
        side,
        Decimal("1"),
        Decimal(price),
        Decimal("0.25"),
        Decimal("0"),
        "paper-atomic-accounting-cost-v1",
        decision,
    )
    PaperSignalWriter(SqlAlchemyPaperSignalRepository(connection)).record(context, signal)
    PaperOrderWriter(SqlAlchemyPaperOrderRepository(connection)).record(context, order)
    PaperFillWriter(SqlAlchemyPaperFillRepository(connection)).record(context, fill, sequence=0)
    return fill


def setup(connection, migrations_dir, *, job_key):
    apply_migrations(connection, migrations_dir)
    instrument_id = uuid4()
    portfolio_id = uuid4()
    run = create_scheduled_job_run(job_key, datetime(2026, 9, 10, 3, 0, tzinfo=UTC))
    context = PaperCycleContext(run)
    connection.execute(
        text(
            "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
            "VALUES (:id, :symbol, 'TEST', 'ACTIVE')"
        ),
        {"id": instrument_id, "symbol": f"ATOMIC-{job_key}"},
    )
    assert SqlAlchemyJobRunRepository(connection).claim(run) is True
    return instrument_id, portfolio_id, run, context


@pytest.mark.integration
def test_paper_accounting_atomically_persists_portfolio_and_transition_derived_pnl() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        instrument_id, portfolio_id, _, context = setup(
            connection,
            migrations_dir,
            job_key="paper-atomic-accounting",
        )
        buy = persist_fill(connection, context, instrument_id, side=OrderSide.BUY, price="100", minute=50)
        sell = persist_fill(connection, context, instrument_id, side=OrderSide.SELL, price="110", minute=52)
        accounting = PaperAccountingWriter(SqlAlchemyPaperAccountingRepository(connection))

        assert accounting.apply_fill(context, portfolio_id, Decimal("1000"), buy) is True
        assert accounting.apply_fill(context, portfolio_id, Decimal("1000"), sell) is True
        assert accounting.apply_fill(context, portfolio_id, Decimal("1000"), sell) is False

        rows = connection.execute(
            text(
                "SELECT fill_id, realized_pnl_delta, commission_delta "
                "FROM paper_portfolio_pnl_events WHERE portfolio_id=:portfolio_id ORDER BY event_time"
            ),
            {"portfolio_id": portfolio_id},
        ).all()
        assert rows == [
            (buy.fill_id, Decimal("0"), Decimal("0.25")),
            (sell.fill_id, Decimal("10"), Decimal("0.25")),
        ]
        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_portfolio_fill_applications "
                "WHERE portfolio_id=:portfolio_id"
            ),
            {"portfolio_id": portfolio_id},
        ).scalar_one() == 2

        restored = SqlAlchemyPaperPortfolioRepository(
            connection
        ).verify_accounting_history(
            portfolio_id,
            current_job_run_id=context.job_run.job_run_id,
        )
        assert restored is not None
        position = restored.state.positions[instrument_id]
        assert position.realized_pnl == Decimal("10")
        assert position.total_commission == Decimal("0.50")


@pytest.mark.integration
def test_atomic_accounting_rejects_incomplete_prior_pnl_without_appending() -> None:
    """A valid new fill cannot extend a prior unproven accounting transition."""
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    try:
        with engine.begin() as connection:
            instrument_id, portfolio_id, run, context = setup(
                connection, migrations_dir, job_key="paper-accounting-prior-pnl-gap",
            )
            first = persist_fill(
                connection, context, instrument_id,
                side=OrderSide.BUY, price="100", minute=40,
            )
            second = persist_fill(
                connection, context, instrument_id,
                side=OrderSide.BUY, price="101", minute=41,
            )
            portfolio = SqlAlchemyPaperPortfolioRepository(connection)
            assert portfolio.apply_fill(
                portfolio_id, Decimal("1000"), first, job_run_id=run.job_run_id,
            )
            before = connection.execute(
                text("SELECT cash, version FROM paper_portfolios WHERE portfolio_id=:id"),
                {"id": portfolio_id},
            ).one()
            accounting = PaperAccountingWriter(SqlAlchemyPaperAccountingRepository(connection))
            for _ in range(2):
                with pytest.raises(
                    RuntimeError, match="PAPER_PORTFOLIO_APPLIED_FILL_WITHOUT_PNL",
                ):
                    accounting.apply_fill(context, portfolio_id, Decimal("1000"), second)
                assert connection.execute(
                    text("SELECT cash, version FROM paper_portfolios WHERE portfolio_id=:id"),
                    {"id": portfolio_id},
                ).one() == before
                assert connection.execute(
                    text("SELECT count(*) FROM paper_portfolio_fill_applications WHERE portfolio_id=:id"),
                    {"id": portfolio_id},
                ).scalar_one() == 1
                assert connection.execute(
                    text("SELECT count(*) FROM paper_portfolio_pnl_events WHERE portfolio_id=:id"),
                    {"id": portfolio_id},
                ).scalar_one() == 0
    finally:
        engine.dispose()


@pytest.mark.integration
def test_public_portfolio_recovery_blocks_concurrent_accounting() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    portfolio_id = uuid4()
    first_run = create_scheduled_job_run(
        "paper-portfolio-recovery-lock-first",
        datetime(2026, 9, 10, 3, 10, tzinfo=UTC),
    )
    second_run = create_scheduled_job_run(
        "paper-portfolio-recovery-lock-second",
        datetime(2026, 9, 10, 3, 11, tzinfo=UTC),
    )
    first_context = PaperCycleContext(first_run)
    second_context = PaperCycleContext(second_run)

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        instrument_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO instruments("
                "instrument_id, canonical_symbol, exchange, status"
                ") VALUES (:id, 'PAPER-RECOVERY-LOCK', 'TEST', 'ACTIVE')"
            ),
            {"id": instrument_id},
        )
        jobs = SqlAlchemyJobRunRepository(connection)
        assert jobs.claim(first_run) is True
        assert jobs.claim(second_run) is True
        first_fill = persist_fill(
            connection,
            first_context,
            instrument_id,
            side=OrderSide.BUY,
            price="100",
            minute=10,
        )
        second_fill = persist_fill(
            connection,
            second_context,
            instrument_id,
            side=OrderSide.BUY,
            price="101",
            minute=11,
        )
        assert PaperAccountingWriter(
            SqlAlchemyPaperAccountingRepository(connection)
        ).apply_fill(
            first_context,
            portfolio_id,
            Decimal("1000"),
            first_fill,
        ) is True
        assert jobs.complete(
            create_job_run_completion(
                first_run,
                JobRunStatus.SUCCEEDED,
                first_run.scheduled_for + timedelta(seconds=30),
            )
        ) is True

    with engine.connect() as recovery_connection:
        recovery_transaction = recovery_connection.begin()
        recovered = SqlAlchemyPaperPortfolioRepository(
            recovery_connection
        ).load_ledger(portfolio_id)
        assert recovered is not None
        assert recovered.applied_fill_ids == frozenset({first_fill.fill_id})

        with pytest.raises(OperationalError, match="lock timeout"):
            with engine.begin() as accounting_connection:
                accounting_connection.execute(text("SET LOCAL lock_timeout = '100ms'"))
                PaperAccountingWriter(
                    SqlAlchemyPaperAccountingRepository(accounting_connection)
                ).apply_fill(
                    second_context,
                    portfolio_id,
                    Decimal("1000"),
                    second_fill,
                )

        recovery_transaction.commit()

    with engine.begin() as connection:
        accounting = PaperAccountingWriter(
            SqlAlchemyPaperAccountingRepository(connection)
        )
        assert accounting.apply_fill(
            second_context,
            portfolio_id,
            Decimal("1000"),
            second_fill,
        ) is True
        assert SqlAlchemyJobRunRepository(connection).complete(
            create_job_run_completion(
                second_run,
                JobRunStatus.SUCCEEDED,
                second_run.scheduled_for + timedelta(seconds=30),
            )
        ) is True
        final = SqlAlchemyPaperPortfolioRepository(connection).load_ledger(
            portfolio_id
        )
        assert final is not None
        assert final.applied_fill_ids == frozenset(
            {first_fill.fill_id, second_fill.fill_id}
        )

    engine.dispose()


@pytest.mark.integration
def test_paper_accounting_takes_job_lock_before_portfolio_mutation() -> None:
    """Accounting and reconciliation share job-then-portfolio lock ordering."""
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    portfolio_id = uuid4()
    run = create_scheduled_job_run(
        "paper-accounting-job-lock-order",
        datetime(2026, 9, 10, 3, 11, 30, tzinfo=UTC),
    )
    context = PaperCycleContext(run)

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        instrument_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO instruments("
                "instrument_id, canonical_symbol, exchange, status"
                ") VALUES (:id, 'PAPER-ACCOUNTING-JOB-LOCK', 'TEST', 'ACTIVE')"
            ),
            {"id": instrument_id},
        )
        assert SqlAlchemyJobRunRepository(connection).claim(run) is True
        fill = persist_fill(
            connection,
            context,
            instrument_id,
            side=OrderSide.BUY,
            price="100",
            minute=14,
        )

    with engine.connect() as reconciliation_connection:
        reconciliation_transaction = reconciliation_connection.begin()
        assert SqlAlchemyJobRunRepository(
            reconciliation_connection
        ).lock_claimed_for_reconciliation(run) is True

        with pytest.raises(OperationalError, match="lock timeout"):
            with engine.begin() as accounting_connection:
                accounting_connection.execute(text("SET LOCAL lock_timeout = '100ms'"))
                PaperAccountingWriter(
                    SqlAlchemyPaperAccountingRepository(accounting_connection)
                ).apply_fill(
                    context,
                    portfolio_id,
                    Decimal("1000"),
                    fill,
                )

        reconciliation_transaction.commit()

    with engine.begin() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM paper_portfolios WHERE portfolio_id=:id"),
            {"id": portfolio_id},
        ).scalar_one() == 0
        assert PaperAccountingWriter(
            SqlAlchemyPaperAccountingRepository(connection)
        ).apply_fill(
            context,
            portfolio_id,
            Decimal("1000"),
            fill,
        ) is True

    engine.dispose()


@pytest.mark.integration
def test_public_portfolio_recovery_blocks_direct_position_mutation() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    portfolio_id = uuid4()
    run = create_scheduled_job_run(
        "paper-portfolio-recovery-position-lock",
        datetime(2026, 9, 10, 3, 12, tzinfo=UTC),
    )
    context = PaperCycleContext(run)

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        instrument_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO instruments("
                "instrument_id, canonical_symbol, exchange, status"
                ") VALUES (:id, 'PAPER-RECOVERY-POSITION-LOCK', 'TEST', 'ACTIVE')"
            ),
            {"id": instrument_id},
        )
        jobs = SqlAlchemyJobRunRepository(connection)
        assert jobs.claim(run) is True
        fill = persist_fill(
            connection,
            context,
            instrument_id,
            side=OrderSide.BUY,
            price="100",
            minute=12,
        )
        assert PaperAccountingWriter(
            SqlAlchemyPaperAccountingRepository(connection)
        ).apply_fill(
            context,
            portfolio_id,
            Decimal("1000"),
            fill,
        ) is True
        assert jobs.complete(
            create_job_run_completion(
                run,
                JobRunStatus.SUCCEEDED,
                run.scheduled_for + timedelta(seconds=30),
            )
        ) is True

    with engine.connect() as recovery_connection:
        recovery_transaction = recovery_connection.begin()
        recovered = SqlAlchemyPaperPortfolioRepository(
            recovery_connection
        ).load_ledger(portfolio_id)
        assert recovered is not None

        with pytest.raises(OperationalError, match="lock timeout"):
            with engine.begin() as mutation_connection:
                mutation_connection.execute(text("SET LOCAL lock_timeout = '100ms'"))
                mutation_connection.execute(
                    text(
                        "UPDATE paper_portfolio_positions "
                        "SET realized_pnl = realized_pnl + 1 "
                        "WHERE portfolio_id=:portfolio_id "
                        "AND instrument_id=:instrument_id"
                    ),
                    {
                        "portfolio_id": portfolio_id,
                        "instrument_id": instrument_id,
                    },
                )

        recovery_transaction.commit()

    with engine.begin() as connection:
        recovered = SqlAlchemyPaperPortfolioRepository(connection).load_ledger(
            portfolio_id
        )
        assert recovered is not None
        assert recovered.state.positions[instrument_id].realized_pnl == Decimal("0")

    engine.dispose()


@pytest.mark.integration
def test_public_portfolio_recovery_blocks_direct_pnl_insertion() -> None:
    """Out-of-band P&L evidence cannot appear while recovery proves history."""
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    portfolio_id = uuid4()
    run = create_scheduled_job_run(
        "paper-portfolio-recovery-pnl-lock",
        datetime(2026, 9, 10, 3, 13, tzinfo=UTC),
    )
    context = PaperCycleContext(run)

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        instrument_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO instruments("
                "instrument_id, canonical_symbol, exchange, status"
                ") VALUES (:id, 'PAPER-RECOVERY-PNL-LOCK', 'TEST', 'ACTIVE')"
            ),
            {"id": instrument_id},
        )
        jobs = SqlAlchemyJobRunRepository(connection)
        assert jobs.claim(run) is True
        fill = persist_fill(
            connection,
            context,
            instrument_id,
            side=OrderSide.BUY,
            price="100",
            minute=13,
        )
        assert PaperAccountingWriter(
            SqlAlchemyPaperAccountingRepository(connection)
        ).apply_fill(
            context,
            portfolio_id,
            Decimal("1000"),
            fill,
        ) is True
        assert jobs.complete(
            create_job_run_completion(
                run,
                JobRunStatus.SUCCEEDED,
                run.scheduled_for + timedelta(seconds=30),
            )
        ) is True

    duplicate_event_id = paper_portfolio_pnl_event_id(portfolio_id, fill.fill_id)
    insert_duplicate = text(
        "INSERT INTO paper_portfolio_pnl_events("
        "pnl_event_id, portfolio_id, fill_id, instrument_id, "
        "realized_pnl_delta, commission_delta, event_time"
        ") VALUES ("
        ":event_id, :portfolio_id, :fill_id, :instrument_id, 0, 0.25, :event_time"
        ")"
    )
    params = {
        "event_id": duplicate_event_id,
        "portfolio_id": portfolio_id,
        "fill_id": fill.fill_id,
        "instrument_id": instrument_id,
        "event_time": fill.fill_time,
    }

    with engine.connect() as recovery_connection:
        recovery_transaction = recovery_connection.begin()
        recovered = SqlAlchemyPaperPortfolioRepository(
            recovery_connection
        ).verify_accounting_history(
            portfolio_id,
            current_job_run_id=run.job_run_id,
        )
        assert recovered is not None

        with pytest.raises(OperationalError, match="lock timeout"):
            with engine.begin() as mutation_connection:
                mutation_connection.execute(text("SET LOCAL lock_timeout = '100ms'"))
                mutation_connection.execute(insert_duplicate, params)

        recovery_transaction.commit()

    with pytest.raises(IntegrityError):
        with engine.begin() as mutation_connection:
            mutation_connection.execute(insert_duplicate, params)

    with engine.begin() as connection:
        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_portfolio_pnl_events "
                "WHERE portfolio_id=:portfolio_id AND fill_id=:fill_id"
            ),
            {"portfolio_id": portfolio_id, "fill_id": fill.fill_id},
        ).scalar_one() == 1

    engine.dispose()


@pytest.mark.integration
def test_paper_accounting_rolls_back_portfolio_when_pnl_persistence_conflicts() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        instrument_id, portfolio_id, run, context = setup(
            connection,
            migrations_dir,
            job_key="paper-atomic-accounting-conflict",
        )
        fill = persist_fill(connection, context, instrument_id, side=OrderSide.BUY, price="100", minute=55)
        event_id = paper_portfolio_pnl_event_id(portfolio_id, fill.fill_id)
        conflict = create_paper_effect(run, PaperEffectType.PNL, event_id, "0" * 64)
        assert SqlAlchemyPaperEffectRepository(connection).record(conflict) is True

        accounting = PaperAccountingWriter(SqlAlchemyPaperAccountingRepository(connection))
        with pytest.raises(ValueError, match="PAPER_EFFECT_IDENTITY_CONFLICT"):
            accounting.apply_fill(context, portfolio_id, Decimal("500"), fill)

        assert connection.execute(
            text("SELECT count(*) FROM paper_portfolios WHERE portfolio_id=:id"),
            {"id": portfolio_id},
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT count(*) FROM paper_portfolio_fill_applications WHERE portfolio_id=:id"),
            {"id": portfolio_id},
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT count(*) FROM paper_portfolio_pnl_events WHERE portfolio_id=:id"),
            {"id": portfolio_id},
        ).scalar_one() == 0


@pytest.mark.integration
def test_paper_accounting_rejects_applied_fill_without_matching_pnl_event() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        instrument_id, portfolio_id, _, context = setup(
            connection,
            migrations_dir,
            job_key="paper-atomic-accounting-partial",
        )
        fill = persist_fill(connection, context, instrument_id, side=OrderSide.BUY, price="100", minute=57)

        legacy_two_step = SqlAlchemyPaperPortfolioRepository(connection)
        assert legacy_two_step.apply_fill_with_transition(
            portfolio_id,
            Decimal("500"),
            fill,
            job_run_id=context.job_run.job_run_id,
        ) is not None

        accounting = PaperAccountingWriter(SqlAlchemyPaperAccountingRepository(connection))
        with pytest.raises(RuntimeError, match="PAPER_ACCOUNTING_APPLIED_FILL_WITHOUT_PNL"):
            accounting.apply_fill(context, portfolio_id, Decimal("500"), fill)

        assert connection.execute(
            text("SELECT count(*) FROM paper_portfolio_fill_applications WHERE portfolio_id=:id"),
            {"id": portfolio_id},
        ).scalar_one() == 1
        assert connection.execute(
            text("SELECT count(*) FROM paper_portfolio_pnl_events WHERE portfolio_id=:id"),
            {"id": portfolio_id},
        ).scalar_one() == 0


@pytest.mark.integration
def test_paper_accounting_requires_reusable_source_fill_ownership() -> None:
    """Unfinished foreign fills must not mutate portfolio or PNL state."""
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        instrument_id, portfolio_id, source_run, source_context = setup(
            connection,
            migrations_dir,
            job_key="paper-accounting-source-fill-owner",
        )
        fill = persist_fill(
            connection,
            source_context,
            instrument_id,
            side=OrderSide.BUY,
            price="100",
            minute=59,
        )
        accounting_run = create_scheduled_job_run(
            "paper-accounting-foreign-fill-consumer",
            datetime(2026, 9, 10, 3, 1, tzinfo=UTC),
        )
        accounting_context = PaperCycleContext(accounting_run)
        jobs = SqlAlchemyJobRunRepository(connection)
        assert jobs.claim(accounting_run) is True
        accounting = PaperAccountingWriter(
            SqlAlchemyPaperAccountingRepository(connection)
        )

        with pytest.raises(ValueError, match="PAPER_EFFECT_IDENTITY_CONFLICT"):
            accounting.apply_fill(
                accounting_context,
                portfolio_id,
                Decimal("500"),
                fill,
            )

        assert connection.execute(
            text("SELECT count(*) FROM paper_portfolios WHERE portfolio_id=:id"),
            {"id": portfolio_id},
        ).scalar_one() == 0
        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_portfolio_fill_applications "
                "WHERE portfolio_id=:id"
            ),
            {"id": portfolio_id},
        ).scalar_one() == 0
        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_portfolio_pnl_events "
                "WHERE portfolio_id=:id"
            ),
            {"id": portfolio_id},
        ).scalar_one() == 0

        assert jobs.complete(
            create_job_run_completion(
                source_run,
                JobRunStatus.SUCCEEDED,
                source_run.scheduled_for + timedelta(seconds=30),
            )
        ) is True
        assert accounting.apply_fill(
            accounting_context,
            portfolio_id,
            Decimal("500"),
            fill,
        ) is True

    engine.dispose()


@pytest.mark.integration
def test_paper_accounting_retry_rejects_pnl_owned_by_another_claimed_run() -> None:
    """Direct retries must not borrow PNL evidence from unfinished foreign work."""
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        instrument_id, portfolio_id, _, context = setup(
            connection,
            migrations_dir,
            job_key="paper-accounting-owned-retry",
        )
        fill = persist_fill(
            connection,
            context,
            instrument_id,
            side=OrderSide.BUY,
            price="100",
            minute=58,
        )
        accounting = PaperAccountingWriter(
            SqlAlchemyPaperAccountingRepository(connection)
        )
        assert accounting.apply_fill(
            context,
            portfolio_id,
            Decimal("500"),
            fill,
        ) is True

        competing_run = create_scheduled_job_run(
            "paper-accounting-foreign-retry",
            datetime(2026, 9, 10, 3, 1, tzinfo=UTC),
        )
        competing_context = PaperCycleContext(competing_run)
        assert SqlAlchemyJobRunRepository(connection).claim(competing_run) is True

        with pytest.raises(ValueError, match="PAPER_EFFECT_IDENTITY_CONFLICT"):
            accounting.apply_fill(
                competing_context,
                portfolio_id,
                Decimal("500"),
                fill,
            )

        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_portfolio_pnl_events "
                "WHERE portfolio_id=:portfolio_id AND fill_id=:fill_id"
            ),
            {"portfolio_id": portfolio_id, "fill_id": fill.fill_id},
        ).scalar_one() == 1
        assert connection.execute(
            text(
                "SELECT version FROM paper_portfolios "
                "WHERE portfolio_id=:portfolio_id"
            ),
            {"portfolio_id": portfolio_id},
        ).scalar_one() == 1

    engine.dispose()
