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
from hope.application.paper.portfolio_pnl import PaperPortfolioPnLWriter
from hope.domain.execution import Environment, Fill, Order, OrderSide
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_fills import SqlAlchemyPaperFillRepository
from hope.infrastructure.repositories.paper_orders import SqlAlchemyPaperOrderRepository
from hope.infrastructure.repositories.paper_portfolio import SqlAlchemyPaperPortfolioRepository
from hope.infrastructure.repositories.paper_portfolio_pnl import SqlAlchemyPaperPortfolioPnLRepository
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository

UTC = timezone.utc
INPUTS_HASH = "a" * 64


def persist_fill(connection, context, instrument_id, side, price, decision_minute):
    decision = datetime(2026, 9, 10, 0, decision_minute, tzinfo=UTC)
    signal_id = context.signal_id(
        instrument_id=instrument_id,
        strategy_version=f"paper-accounting-{decision_minute}",
        decision_time=decision,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"),
        inputs_hash=INPUTS_HASH,
    )
    signal = Signal(
        signal_id=signal_id,
        instrument_id=instrument_id,
        strategy_version=f"paper-accounting-{decision_minute}",
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
        "paper-accounting-cost-v1",
        decision,
    )
    PaperSignalWriter(SqlAlchemyPaperSignalRepository(connection)).record(context, signal)
    PaperOrderWriter(SqlAlchemyPaperOrderRepository(connection)).record(context, order)
    PaperFillWriter(SqlAlchemyPaperFillRepository(connection)).record(context, fill, sequence=0)
    return fill


@pytest.mark.integration
def test_paper_portfolio_pnl_created_at_is_database_authenticated() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id, portfolio_id = uuid4(), uuid4()
    run = create_scheduled_job_run(
        "paper-portfolio-pnl-created-at-auth",
        datetime(2026, 9, 10, 0, 0, tzinfo=UTC),
    )
    context = PaperCycleContext(run)

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text(
                "INSERT INTO instruments("
                "instrument_id, canonical_symbol, exchange, status"
                ") VALUES (:id, 'PAPER-PNL-CREATED-AT', 'TEST', 'ACTIVE')"
            ),
            {"id": instrument_id},
        )
        assert SqlAlchemyJobRunRepository(connection).claim(run)

        fill = persist_fill(
            connection,
            context,
            instrument_id,
            OrderSide.BUY,
            "100",
            1,
        )
        portfolio = SqlAlchemyPaperPortfolioRepository(connection)
        transition = portfolio.apply_fill_with_transition(
            portfolio_id,
            Decimal("1000"),
            fill,
            job_run_id=run.job_run_id,
        )
        assert transition is not None

        with pytest.raises(
            IntegrityError,
            match="PAPER_PORTFOLIO_PNL_TIMESTAMP_NOT_DATABASE_AUTHENTICATED",
        ):
            with connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO paper_portfolio_pnl_events("
                        "pnl_event_id, portfolio_id, fill_id, instrument_id, "
                        "realized_pnl_delta, commission_delta, event_time, created_at"
                        ") VALUES ("
                        ":pnl_event_id, :portfolio_id, :fill_id, :instrument_id, "
                        ":realized_pnl_delta, :commission_delta, :event_time, "
                        "transaction_timestamp() + interval '1 microsecond'"
                        ")"
                    ),
                    {
                        "pnl_event_id": uuid4(),
                        "portfolio_id": portfolio_id,
                        "fill_id": fill.fill_id,
                        "instrument_id": fill.instrument_id,
                        "realized_pnl_delta": transition.realized_pnl_delta,
                        "commission_delta": transition.commission_delta,
                        "event_time": fill.fill_time,
                    },
                )


@pytest.mark.integration
def test_paper_portfolio_pnl_persists_only_transition_derived_accounting() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id, portfolio_id = uuid4(), uuid4()
    run = create_scheduled_job_run("paper-portfolio-pnl", datetime(2026, 9, 10, 0, 0, tzinfo=UTC))
    context = PaperCycleContext(run)

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text("INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) VALUES (:id,'PAPER-PNL-PORT','TEST','ACTIVE')"),
            {"id": instrument_id},
        )
        assert SqlAlchemyJobRunRepository(connection).claim(run)

        buy = persist_fill(connection, context, instrument_id, OrderSide.BUY, "100", 1)
        sell = persist_fill(connection, context, instrument_id, OrderSide.SELL, "110", 2)
        portfolio = SqlAlchemyPaperPortfolioRepository(connection)
        assert portfolio.apply_fill_with_transition(
            portfolio_id,
            Decimal("1000"),
            buy,
            job_run_id=run.job_run_id,
        ) is not None
        sell_transition = portfolio.apply_fill_with_transition(
            portfolio_id,
            Decimal("1000"),
            sell,
            job_run_id=run.job_run_id,
        )
        assert sell_transition is not None
        assert sell_transition.realized_pnl_delta == Decimal("10")
        assert sell_transition.commission_delta == Decimal("0.25")

        writer = PaperPortfolioPnLWriter(SqlAlchemyPaperPortfolioPnLRepository(connection))
        assert writer.record(context, portfolio_id, sell, sell_transition) is True
        assert writer.record(context, portfolio_id, sell, sell_transition) is False

        row = connection.execute(
            text(
                "SELECT portfolio_id, fill_id, instrument_id, realized_pnl_delta, commission_delta "
                "FROM paper_portfolio_pnl_events WHERE portfolio_id=:portfolio_id AND fill_id=:fill_id"
            ),
            {"portfolio_id": portfolio_id, "fill_id": sell.fill_id},
        ).mappings().one()
        assert row["portfolio_id"] == portfolio_id
        assert row["fill_id"] == sell.fill_id
        assert row["instrument_id"] == instrument_id
        assert row["realized_pnl_delta"] == Decimal("10")
        assert row["commission_delta"] == Decimal("0.25")

        with pytest.raises(IntegrityError, match="PAPER_PORTFOLIO_PNL_EVENT_IMMUTABLE"):
            with connection.begin_nested():
                connection.execute(
                    text(
                        "UPDATE paper_portfolio_pnl_events SET realized_pnl_delta=999 "
                        "WHERE portfolio_id=:portfolio_id AND fill_id=:fill_id"
                    ),
                    {"portfolio_id": portfolio_id, "fill_id": sell.fill_id},
                )

        with pytest.raises(IntegrityError, match="PAPER_PORTFOLIO_PNL_EVENT_IMMUTABLE"):
            with connection.begin_nested():
                connection.execute(
                    text(
                        "DELETE FROM paper_portfolio_pnl_events "
                        "WHERE portfolio_id=:portfolio_id AND fill_id=:fill_id"
                    ),
                    {"portfolio_id": portfolio_id, "fill_id": sell.fill_id},
                )

        durable = connection.execute(
            text(
                "SELECT realized_pnl_delta, commission_delta FROM paper_portfolio_pnl_events "
                "WHERE portfolio_id=:portfolio_id AND fill_id=:fill_id"
            ),
            {"portfolio_id": portfolio_id, "fill_id": sell.fill_id},
        ).mappings().one()
        assert durable["realized_pnl_delta"] == Decimal("10")
        assert durable["commission_delta"] == Decimal("0.25")


@pytest.mark.integration
def test_paper_portfolio_pnl_requires_reusable_source_fill_ownership(monkeypatch) -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id, portfolio_id = uuid4(), uuid4()
    source_run = create_scheduled_job_run(
        "paper-portfolio-pnl-source-fill-owner",
        datetime(2026, 9, 10, 0, 3, tzinfo=UTC),
    )
    pnl_run = create_scheduled_job_run(
        "paper-portfolio-pnl-foreign-consumer",
        datetime(2026, 9, 10, 0, 4, tzinfo=UTC),
    )
    source_context = PaperCycleContext(source_run)
    pnl_context = PaperCycleContext(pnl_run)

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text(
                "INSERT INTO instruments("
                "instrument_id, canonical_symbol, exchange, status"
                ") VALUES (:id, 'PAPER-PNL-FILL-OWNER', 'TEST', 'ACTIVE')"
            ),
            {"id": instrument_id},
        )
        jobs = SqlAlchemyJobRunRepository(connection)
        assert jobs.claim(source_run) is True
        fill = persist_fill(
            connection,
            source_context,
            instrument_id,
            OrderSide.BUY,
            "100",
            3,
        )
        transition = SqlAlchemyPaperPortfolioRepository(
            connection
        ).apply_fill_with_transition(
            portfolio_id,
            Decimal("1000"),
            fill,
            job_run_id=source_run.job_run_id,
        )
        assert transition is not None

        assert jobs.claim(pnl_run) is True
        writer = PaperPortfolioPnLWriter(
            SqlAlchemyPaperPortfolioPnLRepository(connection)
        )
        with pytest.raises(ValueError, match="PAPER_EFFECT_IDENTITY_CONFLICT"):
            writer.record(pnl_context, portfolio_id, fill, transition)

        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_portfolio_pnl_events "
                "WHERE portfolio_id=:portfolio_id AND fill_id=:fill_id"
            ),
            {"portfolio_id": portfolio_id, "fill_id": fill.fill_id},
        ).scalar_one() == 0
        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_effects "
                "WHERE effect_type='PNL' AND job_run_id=:job_run_id"
            ),
            {"job_run_id": pnl_run.job_run_id},
        ).scalar_one() == 0

        assert jobs.complete(
            create_job_run_completion(
                source_run,
                JobRunStatus.SUCCEEDED,
                source_run.scheduled_for + timedelta(seconds=30),
            )
        ) is True
        assert writer.record(pnl_context, portfolio_id, fill, transition) is True

        owners = dict(
            connection.execute(
                text(
                    "SELECT effect_type, job_run_id FROM paper_effects "
                    "WHERE (effect_type='FILL' AND entity_id=:fill_id) "
                    "OR (effect_type='PNL' AND job_run_id=:pnl_run_id)"
                ),
                {"fill_id": fill.fill_id, "pnl_run_id": pnl_run.job_run_id},
            ).all()
        )
        assert owners == {
            "FILL": source_run.job_run_id,
            "PNL": pnl_run.job_run_id,
        }

        repository = SqlAlchemyPaperPortfolioPnLRepository(connection)
        assert repository.get(portfolio_id, fill.fill_id) is not None
        monkeypatch.setattr(
            repository._effects,
            "get_reusable_for_job",
            lambda *args, **kwargs: None,
        )
        with pytest.raises(
            ValueError,
            match="PAPER_PORTFOLIO_PNL_SOURCE_FILL_LINEAGE_CONFLICT",
        ):
            repository.get(portfolio_id, fill.fill_id)

    engine.dispose()


@pytest.mark.integration
def test_paper_portfolio_recovery_requires_successful_pnl_owner() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id, portfolio_id = uuid4(), uuid4()
    run = create_scheduled_job_run(
        "paper-portfolio-recovery-pnl-owner",
        datetime(2026, 9, 10, 0, 6, tzinfo=UTC),
    )
    context = PaperCycleContext(run)

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text(
                "INSERT INTO instruments("
                "instrument_id, canonical_symbol, exchange, status"
                ") VALUES (:id, 'PAPER-PNL-RECOVERY-OWNER', 'TEST', 'ACTIVE')"
            ),
            {"id": instrument_id},
        )
        jobs = SqlAlchemyJobRunRepository(connection)
        assert jobs.claim(run) is True
        fill = persist_fill(
            connection,
            context,
            instrument_id,
            OrderSide.BUY,
            "100",
            7,
        )
        portfolio = SqlAlchemyPaperPortfolioRepository(connection)
        transition = portfolio.apply_fill_with_transition(
            portfolio_id,
            Decimal("1000"),
            fill,
            job_run_id=run.job_run_id,
        )
        assert transition is not None
        writer = PaperPortfolioPnLWriter(
            SqlAlchemyPaperPortfolioPnLRepository(connection)
        )
        assert writer.record(context, portfolio_id, fill, transition) is True

        with pytest.raises(
            RuntimeError,
            match="PAPER_PORTFOLIO_PNL_OWNER_NOT_RECOVERABLE",
        ):
            portfolio.load_ledger(portfolio_id)

        assert jobs.complete(
            create_job_run_completion(
                run,
                JobRunStatus.SUCCEEDED,
                run.scheduled_for + timedelta(seconds=30),
            )
        ) is True
        recovered = portfolio.load_ledger(portfolio_id)
        assert recovered is not None
        assert recovered.applied_fill_ids == (fill.fill_id,)

    engine.dispose()
