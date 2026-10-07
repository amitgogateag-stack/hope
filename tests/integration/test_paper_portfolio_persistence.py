import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.application.jobs import (
    JobRunStatus,
    create_job_run_completion,
    create_scheduled_job_run,
)
from hope.application.paper import PaperCycleContext, PaperOrderWriter, PaperSignalWriter
from hope.application.paper.fills import PaperFillWriter
from hope.domain.execution import Environment, Fill, Order, OrderSide
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_fills import SqlAlchemyPaperFillRepository
from hope.infrastructure.repositories.paper_orders import SqlAlchemyPaperOrderRepository
from hope.infrastructure.repositories.paper_portfolio import SqlAlchemyPaperPortfolioRepository
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository

UTC = timezone.utc
INPUTS_HASH = "f" * 64


def persist_fill(connection, context, instrument_id, *, side, quantity, price, sequence, decision_minute):
    decision = datetime(2026, 9, 9, 23, decision_minute, tzinfo=UTC)
    signal_id = context.signal_id(
        instrument_id=instrument_id,
        strategy_version=f"paper-portfolio-{decision_minute}",
        decision_time=decision,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"),
        inputs_hash=INPUTS_HASH,
    )
    signal = Signal(
        signal_id=signal_id,
        instrument_id=instrument_id,
        strategy_version=f"paper-portfolio-{decision_minute}",
        decision_time=decision,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"),
        inputs_hash=INPUTS_HASH,
    )
    order = Order(
        order_id=context.order_id(signal_id),
        signal_id=signal_id,
        instrument_id=instrument_id,
        side=side,
        quantity=quantity,
        environment=Environment.PAPER,
        signal_type=SignalType.ENTRY,
    )
    fill = Fill(
        context.fill_id(signal_id, sequence),
        order.order_id,
        signal_id,
        instrument_id,
        side,
        quantity,
        price,
        Decimal("0.25") if side is OrderSide.BUY else Decimal("0.20"),
        Decimal("0.10"),
        "portfolio-cost-v1",
        decision.replace(minute=decision_minute + 1),
    )
    PaperSignalWriter(SqlAlchemyPaperSignalRepository(connection)).record(context, signal)
    PaperOrderWriter(SqlAlchemyPaperOrderRepository(connection)).record(context, order)
    PaperFillWriter(SqlAlchemyPaperFillRepository(connection)).record(context, fill, sequence=sequence)
    return fill


@pytest.mark.integration
def test_paper_portfolio_materializes_full_state_and_restores_applied_fill_history():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id, portfolio_id = uuid4(), uuid4()
    run = create_scheduled_job_run("paper-portfolio", datetime(2026, 9, 9, 23, 20, tzinfo=UTC))
    context = PaperCycleContext(run)

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text("INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) VALUES (:id,'PAPER-PORT','TEST','ACTIVE')"),
            {"id": instrument_id},
        )
        assert SqlAlchemyJobRunRepository(connection).claim(run)
        buy = persist_fill(
            connection, context, instrument_id,
            side=OrderSide.BUY, quantity=Decimal("2"), price=Decimal("101.5"), sequence=0, decision_minute=21,
        )
        sell = persist_fill(
            connection, context, instrument_id,
            side=OrderSide.SELL, quantity=Decimal("1"), price=Decimal("110"), sequence=0, decision_minute=23,
        )
        repository = SqlAlchemyPaperPortfolioRepository(connection)
        buy_transition = repository.apply_fill_with_transition(
            portfolio_id,
            Decimal("1000"),
            buy,
            job_run_id=run.job_run_id,
        )
        assert buy_transition is not None
        assert buy_transition.realized_pnl_delta == Decimal("0")
        assert buy_transition.commission_delta == Decimal("0.25")
        assert buy_transition.cash_delta == Decimal("-203.25")

        sell_transition = repository.apply_fill_with_transition(
            portfolio_id,
            Decimal("1000"),
            sell,
            job_run_id=run.job_run_id,
        )
        assert sell_transition is not None
        assert sell_transition.state_before == buy_transition.state_after
        assert sell_transition.realized_pnl_delta == Decimal("8.5")
        assert sell_transition.commission_delta == Decimal("0.20")
        assert sell_transition.cash_delta == Decimal("109.80")
        assert repository.apply_fill(
            portfolio_id,
            Decimal("1000"),
            sell,
            job_run_id=run.job_run_id,
        ) is False

        restored = repository._load_materialized_ledger(portfolio_id)
        assert restored is not None
        position = restored.state.positions[instrument_id]
        assert restored.initial_cash == Decimal("1000")
        assert restored.state.cash == Decimal("906.55")
        assert position.quantity == Decimal("1")
        assert position.average_price == Decimal("101.5")
        assert position.realized_pnl == Decimal("8.5")
        assert position.total_commission == Decimal("0.45")
        assert restored.applied_fill_ids == frozenset({buy.fill_id, sell.fill_id})
        assert connection.execute(
            text("SELECT version FROM paper_portfolios WHERE portfolio_id=:id"), {"id": portfolio_id}
        ).scalar_one() == 2


@pytest.mark.integration
def test_paper_portfolio_rejects_untracked_or_conflicting_fill_without_state_mutation():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    portfolio_id, instrument_id = uuid4(), uuid4()
    rogue = Fill(
        uuid4(), uuid4(), uuid4(), instrument_id, OrderSide.BUY,
        Decimal("1"), Decimal("50"), Decimal("0"), Decimal("0"), "rogue-cost-v1",
        datetime(2026, 9, 9, 23, 30, tzinfo=UTC),
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        repository = SqlAlchemyPaperPortfolioRepository(connection)
        with pytest.raises(ValueError, match="PAPER_PORTFOLIO_FILL_UNTRACKED"):
            repository.apply_fill(
                portfolio_id,
                Decimal("500"),
                rogue,
                job_run_id=uuid4(),
            )
        assert connection.execute(text("SELECT count(*) FROM paper_portfolios WHERE portfolio_id=:id"), {"id": portfolio_id}).scalar_one() == 0


@pytest.mark.integration
def test_paper_portfolio_requires_reusable_source_fill_ownership() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id, portfolio_id = uuid4(), uuid4()
    source_run = create_scheduled_job_run(
        "paper-portfolio-source-fill-owner",
        datetime(2026, 9, 9, 23, 25, tzinfo=UTC),
    )
    consumer_run = create_scheduled_job_run(
        "paper-portfolio-foreign-fill-consumer",
        datetime(2026, 9, 9, 23, 27, tzinfo=UTC),
    )
    source_context = PaperCycleContext(source_run)

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text(
                "INSERT INTO instruments("
                "instrument_id, canonical_symbol, exchange, status"
                ") VALUES (:id, 'PAPER-PORT-OWNER', 'TEST', 'ACTIVE')"
            ),
            {"id": instrument_id},
        )
        jobs = SqlAlchemyJobRunRepository(connection)
        assert jobs.claim(source_run) is True
        fill = persist_fill(
            connection,
            source_context,
            instrument_id,
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            price=Decimal("100"),
            sequence=0,
            decision_minute=26,
        )
        assert jobs.claim(consumer_run) is True
        repository = SqlAlchemyPaperPortfolioRepository(connection)

        with pytest.raises(ValueError, match="PAPER_EFFECT_IDENTITY_CONFLICT"):
            repository.apply_fill(
                portfolio_id,
                Decimal("1000"),
                fill,
                job_run_id=consumer_run.job_run_id,
            )

        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_portfolios "
                "WHERE portfolio_id=:portfolio_id"
            ),
            {"portfolio_id": portfolio_id},
        ).scalar_one() == 0
        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_portfolio_fill_applications "
                "WHERE portfolio_id=:portfolio_id"
            ),
            {"portfolio_id": portfolio_id},
        ).scalar_one() == 0

        assert jobs.complete(
            create_job_run_completion(
                source_run,
                JobRunStatus.SUCCEEDED,
                source_run.scheduled_for + timedelta(seconds=30),
            )
        ) is True
        assert repository.apply_fill(
            portfolio_id,
            Decimal("1000"),
            fill,
            job_run_id=consumer_run.job_run_id,
        ) is True

    engine.dispose()


@pytest.mark.integration
def test_paper_portfolio_restore_rejects_applied_fill_without_effect():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id, signal_id, order_id, fill_id, portfolio_id = (uuid4() for _ in range(5))
    decision_time = datetime(2026, 9, 9, 23, 35, tzinfo=UTC)
    fill_time = decision_time.replace(minute=36)

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text("INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) VALUES (:id,'PAPER-NO-EFFECT','TEST','ACTIVE')"),
            {"id": instrument_id},
        )
        connection.execute(
            text(
                "INSERT INTO signals(signal_id, instrument_id, decision_time, state) "
                "VALUES (:signal_id, :instrument_id, :decision_time, 'SIGNAL')"
            ),
            {"signal_id": signal_id, "instrument_id": instrument_id, "decision_time": decision_time},
        )
        connection.execute(
            text(
                "INSERT INTO orders(order_id, signal_id, instrument_id, environment, side, quantity) "
                "VALUES (:order_id, :signal_id, :instrument_id, 'PAPER', 'BUY', 1)"
            ),
            {"order_id": order_id, "signal_id": signal_id, "instrument_id": instrument_id},
        )
        connection.execute(
            text(
                "INSERT INTO fills(fill_id, order_id, quantity, fill_price, slippage, transaction_cost, filled_at, cost_model_version) "
                "VALUES (:fill_id, :order_id, 1, 100, 0, 0.25, :fill_time, 'portfolio-cost-v1')"
            ),
            {"fill_id": fill_id, "order_id": order_id, "fill_time": fill_time},
        )
        connection.execute(
            text(
                "INSERT INTO paper_portfolios(portfolio_id, initial_cash, cash, version) "
                "VALUES (:portfolio_id, 1000, 899.75, 1)"
            ),
            {"portfolio_id": portfolio_id},
        )
        connection.execute(
            text(
                "INSERT INTO paper_portfolio_positions(portfolio_id, instrument_id, quantity, average_price, realized_pnl, total_commission) "
                "VALUES (:portfolio_id, :instrument_id, 1, 100, 0, 0.25)"
            ),
            {"portfolio_id": portfolio_id, "instrument_id": instrument_id},
        )
        connection.execute(
            text(
                "INSERT INTO paper_portfolio_fill_applications(portfolio_id, fill_id, application_sequence, applied_at) "
                "VALUES (:portfolio_id, :fill_id, 1, :fill_time)"
            ),
            {"portfolio_id": portfolio_id, "fill_id": fill_id, "fill_time": fill_time},
        )

        with pytest.raises(RuntimeError, match="PAPER_PORTFOLIO_APPLIED_FILL_WITHOUT_EFFECT"):
            SqlAlchemyPaperPortfolioRepository(connection).load_ledger(portfolio_id)


@pytest.mark.integration
def test_paper_portfolio_accounting_recovery_rejects_applied_fill_without_pnl():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id, portfolio_id = uuid4(), uuid4()
    run = create_scheduled_job_run(
        "paper-portfolio-accounting-recovery",
        datetime(2026, 9, 9, 23, 32, tzinfo=UTC),
    )
    context = PaperCycleContext(run)

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text("INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) VALUES (:id,'PAPER-ACCOUNTING-RECOVERY','TEST','ACTIVE')"),
            {"id": instrument_id},
        )
        assert SqlAlchemyJobRunRepository(connection).claim(run)
        fill = persist_fill(
            connection, context, instrument_id,
            side=OrderSide.BUY, quantity=Decimal("1"), price=Decimal("100"), sequence=0, decision_minute=33,
        )
        repository = SqlAlchemyPaperPortfolioRepository(connection)
        assert repository.apply_fill(
            portfolio_id,
            Decimal("1000"),
            fill,
            job_run_id=run.job_run_id,
        )

        with pytest.raises(RuntimeError, match="PAPER_PORTFOLIO_APPLIED_FILL_WITHOUT_PNL"):
            repository.load_ledger(portfolio_id)

    engine.dispose()


@pytest.mark.integration
def test_paper_fill_transition_recovery_requires_complete_accounting_history():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id, portfolio_id = uuid4(), uuid4()
    run = create_scheduled_job_run("paper-fill-transition-accounting-recovery", datetime(2026, 9, 9, 23, 35, tzinfo=UTC))
    context = PaperCycleContext(run)
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(text("INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) VALUES (:id,'PAPER-TRANSITION-RECOVERY','TEST','ACTIVE')"), {"id": instrument_id})
        assert SqlAlchemyJobRunRepository(connection).claim(run)
        fill = persist_fill(connection, context, instrument_id, side=OrderSide.BUY, quantity=Decimal("1"), price=Decimal("100"), sequence=0, decision_minute=36)
        repository = SqlAlchemyPaperPortfolioRepository(connection)
        assert repository.apply_fill_with_transition(
            portfolio_id,
            Decimal("1000"),
            fill,
            job_run_id=run.job_run_id,
        ) is not None
        with pytest.raises(RuntimeError, match="PAPER_PORTFOLIO_APPLIED_FILL_WITHOUT_PNL"):
            repository.load_fill_transition(portfolio_id, fill.fill_id)
    engine.dispose()


@pytest.mark.integration
def test_paper_portfolio_restore_rejects_incomplete_application_history():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id, portfolio_id = uuid4(), uuid4()
    run = create_scheduled_job_run("paper-portfolio-history", datetime(2026, 9, 9, 23, 40, tzinfo=UTC))
    context = PaperCycleContext(run)

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text("INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) VALUES (:id,'PAPER-HISTORY','TEST','ACTIVE')"),
            {"id": instrument_id},
        )
        assert SqlAlchemyJobRunRepository(connection).claim(run)
        first = persist_fill(
            connection, context, instrument_id,
            side=OrderSide.BUY, quantity=Decimal("1"), price=Decimal("100"), sequence=0, decision_minute=41,
        )
        second = persist_fill(
            connection, context, instrument_id,
            side=OrderSide.BUY, quantity=Decimal("1"), price=Decimal("101"), sequence=0, decision_minute=43,
        )
        repository = SqlAlchemyPaperPortfolioRepository(connection)
        assert repository.apply_fill(
            portfolio_id,
            Decimal("1000"),
            first,
            job_run_id=run.job_run_id,
        )
        assert repository.apply_fill(
            portfolio_id,
            Decimal("1000"),
            second,
            job_run_id=run.job_run_id,
        )

        connection.execute(
            text("UPDATE paper_portfolios SET version = 3 WHERE portfolio_id=:portfolio_id"),
            {"portfolio_id": portfolio_id},
        )

        with pytest.raises(RuntimeError, match="PAPER_PORTFOLIO_APPLICATION_HISTORY_INCONSISTENT"):
            repository.load_ledger(portfolio_id)


@pytest.mark.integration
def test_paper_portfolio_restore_rejects_corrupt_materialized_state():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id, portfolio_id = uuid4(), uuid4()
    run = create_scheduled_job_run("paper-portfolio-corrupt-state", datetime(2026, 9, 9, 23, 42, tzinfo=UTC))
    context = PaperCycleContext(run)

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text("INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) VALUES (:id,'PAPER-CORRUPT','TEST','ACTIVE')"),
            {"id": instrument_id},
        )
        assert SqlAlchemyJobRunRepository(connection).claim(run)
        fill = persist_fill(
            connection, context, instrument_id,
            side=OrderSide.BUY, quantity=Decimal("1"), price=Decimal("100"), sequence=0, decision_minute=43,
        )
        repository = SqlAlchemyPaperPortfolioRepository(connection)
        assert repository.apply_fill(
            portfolio_id,
            Decimal("1000"),
            fill,
            job_run_id=run.job_run_id,
        )

        connection.execute(
            text("UPDATE paper_portfolios SET cash = cash + 1 WHERE portfolio_id=:portfolio_id"),
            {"portfolio_id": portfolio_id},
        )

        with pytest.raises(RuntimeError, match="PAPER_PORTFOLIO_MATERIALIZED_STATE_INCONSISTENT"):
            repository.load_ledger(portfolio_id)


@pytest.mark.integration
def test_paper_portfolio_rejects_fill_time_regression_without_state_mutation():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id, portfolio_id = uuid4(), uuid4()
    run = create_scheduled_job_run("paper-portfolio-chronology", datetime(2026, 9, 9, 23, 45, tzinfo=UTC))
    context = PaperCycleContext(run)

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text("INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) VALUES (:id,'PAPER-CHRONO','TEST','ACTIVE')"),
            {"id": instrument_id},
        )
        assert SqlAlchemyJobRunRepository(connection).claim(run)
        earlier = persist_fill(
            connection, context, instrument_id,
            side=OrderSide.BUY, quantity=Decimal("1"), price=Decimal("100"), sequence=0, decision_minute=46,
        )
        later = persist_fill(
            connection, context, instrument_id,
            side=OrderSide.SELL, quantity=Decimal("1"), price=Decimal("110"), sequence=0, decision_minute=48,
        )
        repository = SqlAlchemyPaperPortfolioRepository(connection)
        assert repository.apply_fill(
            portfolio_id,
            Decimal("1000"),
            later,
            job_run_id=run.job_run_id,
        )
        before = connection.execute(
            text("SELECT cash, version FROM paper_portfolios WHERE portfolio_id=:id"),
            {"id": portfolio_id},
        ).one()

        with pytest.raises(ValueError, match="PAPER_PORTFOLIO_FILL_TIME_REGRESSION"):
            repository.apply_fill(
                portfolio_id,
                Decimal("1000"),
                earlier,
                job_run_id=run.job_run_id,
            )

        after = connection.execute(
            text("SELECT cash, version FROM paper_portfolios WHERE portfolio_id=:id"),
            {"id": portfolio_id},
        ).one()
        assert after == before
        assert connection.execute(
            text("SELECT count(*) FROM paper_portfolio_fill_applications WHERE portfolio_id=:id"),
            {"id": portfolio_id},
        ).scalar_one() == 1

@pytest.mark.integration
def test_paper_portfolio_application_timestamp_is_database_authenticated() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id, portfolio_id = uuid4(), uuid4()
    run = create_scheduled_job_run(
        "paper-portfolio-applied-at-auth",
        datetime(2026, 9, 9, 23, 50, tzinfo=UTC),
    )
    context = PaperCycleContext(run)

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            apply_migrations(connection, migrations_dir)
            connection.execute(
                text(
                    "INSERT INTO instruments("
                    "instrument_id, canonical_symbol, exchange, status"
                    ") VALUES ("
                    ":id, 'PAPER-APPLICATION-CREATED-AT', 'TEST', 'ACTIVE'"
                    ")"
                ),
                {"id": instrument_id},
            )
            assert SqlAlchemyJobRunRepository(connection).claim(run)
            fill = persist_fill(
                connection,
                context,
                instrument_id,
                side=OrderSide.BUY,
                quantity=Decimal("1"),
                price=Decimal("100"),
                sequence=0,
                decision_minute=51,
            )
            connection.execute(
                text(
                    "INSERT INTO paper_portfolios("
                    "portfolio_id, initial_cash, cash"
                    ") VALUES (:portfolio_id, 1000, 1000)"
                ),
                {"portfolio_id": portfolio_id},
            )

            with pytest.raises(
                IntegrityError,
                match=(
                    "PAPER_PORTFOLIO_APPLICATION_TIMESTAMP_"
                    "NOT_DATABASE_AUTHENTICATED"
                ),
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO paper_portfolio_fill_applications("
                            "portfolio_id, fill_id, application_sequence, applied_at"
                            ") VALUES ("
                            ":portfolio_id, :fill_id, 1, "
                            "transaction_timestamp() + interval '1 microsecond'"
                            ")"
                        ),
                        {
                            "portfolio_id": portfolio_id,
                            "fill_id": fill.fill_id,
                        },
                    )
        finally:
            transaction.rollback()
            engine.dispose()


@pytest.mark.integration
def test_paper_portfolio_updated_at_is_database_managed() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    portfolio_id = uuid4()

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            apply_migrations(connection, migrations_dir)
            connection.execute(
                text(
                    "INSERT INTO paper_portfolios("
                    "portfolio_id, initial_cash, cash"
                    ") VALUES (:portfolio_id, 1000, 1000)"
                ),
                {"portfolio_id": portfolio_id},
            )
            original = connection.execute(
                text(
                    "SELECT created_at, updated_at "
                    "FROM paper_portfolios WHERE portfolio_id=:portfolio_id"
                ),
                {"portfolio_id": portfolio_id},
            ).mappings().one()
            assert original["updated_at"] == original["created_at"]

            with pytest.raises(
                IntegrityError,
                match="PAPER_PORTFOLIO_UPDATED_AT_CALLER_FORBIDDEN",
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE paper_portfolios "
                            "SET cash=999, "
                            "updated_at=transaction_timestamp() - interval '1 second' "
                            "WHERE portfolio_id=:portfolio_id"
                        ),
                        {"portfolio_id": portfolio_id},
                    )

            connection.execute(
                text(
                    "UPDATE paper_portfolios "
                    "SET cash=999 WHERE portfolio_id=:portfolio_id"
                ),
                {"portfolio_id": portfolio_id},
            )
            updated = connection.execute(
                text(
                    "SELECT created_at, updated_at, cash "
                    "FROM paper_portfolios WHERE portfolio_id=:portfolio_id"
                ),
                {"portfolio_id": portfolio_id},
            ).mappings().one()
            assert updated["created_at"] == original["created_at"]
            assert updated["updated_at"] >= original["updated_at"]
            assert updated["cash"] == Decimal("999")
        finally:
            transaction.rollback()
            engine.dispose()


@pytest.mark.integration
def test_paper_portfolio_created_at_is_database_authenticated_and_immutable() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    portfolio_id = uuid4()

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            apply_migrations(connection, migrations_dir)

            with pytest.raises(
                IntegrityError,
                match="PAPER_PORTFOLIO_TIMESTAMP_NOT_DATABASE_AUTHENTICATED",
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO paper_portfolios("
                            "portfolio_id, initial_cash, cash, created_at"
                            ") VALUES ("
                            ":portfolio_id, 1000, 1000, "
                            "transaction_timestamp() - interval '1 microsecond'"
                            ")"
                        ),
                        {"portfolio_id": portfolio_id},
                    )

            connection.execute(
                text(
                    "INSERT INTO paper_portfolios("
                    "portfolio_id, initial_cash, cash"
                    ") VALUES (:portfolio_id, 1000, 1000)"
                ),
                {"portfolio_id": portfolio_id},
            )

            with pytest.raises(
                IntegrityError,
                match="PAPER_PORTFOLIO_CREATED_AT_IMMUTABLE",
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE paper_portfolios "
                            "SET created_at = created_at + interval '1 microsecond' "
                            "WHERE portfolio_id = :portfolio_id"
                        ),
                        {"portfolio_id": portfolio_id},
                    )
        finally:
            transaction.rollback()
            engine.dispose()


@pytest.mark.integration
def test_paper_portfolio_projection_rows_cannot_be_deleted() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    portfolio_id, instrument_id = uuid4(), uuid4()

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            apply_migrations(connection, migrations_dir)
            connection.execute(
                text(
                    "INSERT INTO instruments("
                    "instrument_id, canonical_symbol, exchange, status"
                    ") VALUES ("
                    ":instrument_id, 'PAPER-PORTFOLIO-DELETE', 'TEST', 'ACTIVE'"
                    ")"
                ),
                {"instrument_id": instrument_id},
            )
            connection.execute(
                text(
                    "INSERT INTO paper_portfolios("
                    "portfolio_id, initial_cash, cash"
                    ") VALUES (:portfolio_id, 1000, 900)"
                ),
                {"portfolio_id": portfolio_id},
            )
            connection.execute(
                text(
                    "INSERT INTO paper_portfolio_positions("
                    "portfolio_id, instrument_id, quantity, average_price, "
                    "realized_pnl, total_commission"
                    ") VALUES ("
                    ":portfolio_id, :instrument_id, 1, 100, 0, 0"
                    ")"
                ),
                {
                    "portfolio_id": portfolio_id,
                    "instrument_id": instrument_id,
                },
            )

            with pytest.raises(
                IntegrityError,
                match="PAPER_PORTFOLIO_POSITION_DELETE_FORBIDDEN",
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "DELETE FROM paper_portfolio_positions "
                            "WHERE portfolio_id=:portfolio_id "
                            "AND instrument_id=:instrument_id"
                        ),
                        {
                            "portfolio_id": portfolio_id,
                            "instrument_id": instrument_id,
                        },
                    )

            with pytest.raises(
                IntegrityError,
                match="PAPER_PORTFOLIO_DELETE_FORBIDDEN",
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "DELETE FROM paper_portfolios "
                            "WHERE portfolio_id=:portfolio_id"
                        ),
                        {"portfolio_id": portfolio_id},
                    )
        finally:
            transaction.rollback()
            engine.dispose()


@pytest.mark.integration
def test_paper_portfolio_projection_identities_are_immutable() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    portfolio_id, replacement_portfolio_id = uuid4(), uuid4()
    instrument_id, replacement_instrument_id = uuid4(), uuid4()

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            apply_migrations(connection, migrations_dir)
            connection.execute(
                text(
                    "INSERT INTO instruments("
                    "instrument_id, canonical_symbol, exchange, status"
                    ") VALUES "
                    "(:instrument_id, 'PAPER-POSITION-IDENTITY', 'TEST', 'ACTIVE'), "
                    "(:replacement_instrument_id, "
                    "'PAPER-POSITION-IDENTITY-REPLACEMENT', 'TEST', 'ACTIVE')"
                ),
                {
                    "instrument_id": instrument_id,
                    "replacement_instrument_id": replacement_instrument_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO paper_portfolios("
                    "portfolio_id, initial_cash, cash"
                    ") VALUES (:portfolio_id, 1000, 900)"
                ),
                {"portfolio_id": portfolio_id},
            )

            with pytest.raises(
                IntegrityError,
                match="PAPER_PORTFOLIO_ID_IMMUTABLE",
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE paper_portfolios "
                            "SET portfolio_id=:replacement_portfolio_id "
                            "WHERE portfolio_id=:portfolio_id"
                        ),
                        {
                            "portfolio_id": portfolio_id,
                            "replacement_portfolio_id": replacement_portfolio_id,
                        },
                    )

            connection.execute(
                text(
                    "INSERT INTO paper_portfolio_positions("
                    "portfolio_id, instrument_id, quantity, average_price, "
                    "realized_pnl, total_commission"
                    ") VALUES ("
                    ":portfolio_id, :instrument_id, 1, 100, 0, 0"
                    ")"
                ),
                {
                    "portfolio_id": portfolio_id,
                    "instrument_id": instrument_id,
                },
            )

            with pytest.raises(
                IntegrityError,
                match="PAPER_PORTFOLIO_POSITION_IDENTITY_IMMUTABLE",
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE paper_portfolio_positions "
                            "SET instrument_id=:replacement_instrument_id "
                            "WHERE portfolio_id=:portfolio_id "
                            "AND instrument_id=:instrument_id"
                        ),
                        {
                            "portfolio_id": portfolio_id,
                            "instrument_id": instrument_id,
                            "replacement_instrument_id": replacement_instrument_id,
                        },
                    )

            with pytest.raises(
                IntegrityError,
                match="PAPER_PORTFOLIO_POSITION_IDENTITY_IMMUTABLE",
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE paper_portfolio_positions "
                            "SET portfolio_id=:replacement_portfolio_id "
                            "WHERE portfolio_id=:portfolio_id "
                            "AND instrument_id=:instrument_id"
                        ),
                        {
                            "portfolio_id": portfolio_id,
                            "replacement_portfolio_id": replacement_portfolio_id,
                            "instrument_id": instrument_id,
                        },
                    )

            with pytest.raises(
                IntegrityError,
                match="PAPER_PORTFOLIO_INITIAL_CASH_IMMUTABLE",
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE paper_portfolios "
                            "SET initial_cash=initial_cash + 1 "
                            "WHERE portfolio_id=:portfolio_id"
                        ),
                        {"portfolio_id": portfolio_id},
                    )
        finally:
            transaction.rollback()
            engine.dispose()

