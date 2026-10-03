import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from hope.application.jobs import JobRunStatus, create_scheduled_job_run
from hope.application.paper import (
    PaperCycleContext,
    PaperFillAccountingWriter,
    PaperOrderWriter,
    PaperRiskWriter,
    PaperSignalWriter,
)
from hope.application.paper.effects import PaperEffectType, create_paper_effect
from hope.application.paper.portfolio_pnl import paper_portfolio_pnl_event_id
from hope.domain.execution import Environment, Fill, Order, OrderSide
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository
from hope.infrastructure.repositories.paper_fill_accounting import SqlAlchemyPaperFillAccountingRepository
from hope.infrastructure.repositories.paper_orders import SqlAlchemyPaperOrderRepository
from hope.infrastructure.repositories.paper_risk import SqlAlchemyPaperRiskRepository
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository
from hope.infrastructure.scheduling.paper_reconciliation import reconcile_completed_paper_run

UTC = timezone.utc
INPUTS_HASH = "f" * 64


def _chain(context, instrument_id):
    decision = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    signal_id = context.signal_id(
        instrument_id=instrument_id,
        strategy_version="paper-accounting-restart-v1",
        decision_time=decision,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"),
        inputs_hash=INPUTS_HASH,
    )
    signal = Signal(
        signal_id=signal_id,
        instrument_id=instrument_id,
        strategy_version="paper-accounting-restart-v1",
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
    fill = Fill(
        context.fill_id(signal_id, 0),
        order.order_id,
        signal_id,
        instrument_id,
        OrderSide.BUY,
        Decimal("2"),
        Decimal("100"),
        Decimal("0.50"),
        Decimal("0"),
        "paper-accounting-restart-cost-v1",
        decision,
    )
    return signal, order, fill


def _setup(connection, migrations_dir, job_key, *, authenticated_job=False):
    apply_migrations(connection, migrations_dir)
    instrument_id, portfolio_id = uuid4(), uuid4()
    durable_job_key = job_key
    if authenticated_job:
        strategy_id, strategy_version_id = uuid4(), uuid4()
        connection.execute(
            text("INSERT INTO strategies(strategy_id, name, family) VALUES (:id, :name, 'TEST')"),
            {"id": strategy_id, "name": f"PAPER_ACCOUNTING_{strategy_id}"},
        )
        connection.execute(
            text(
                "INSERT INTO strategy_versions(strategy_version_id, strategy_id, version, code_commit) "
                "VALUES (:version_id, :strategy_id, 'v1', 'paper-accounting-restart-test')"
            ),
            {"version_id": strategy_version_id, "strategy_id": strategy_id},
        )
        durable_job_key = f"paper:USA:{strategy_version_id}:{job_key}"
    run = create_scheduled_job_run(durable_job_key, datetime(2026, 9, 30, 12, 0, tzinfo=UTC))
    context = PaperCycleContext(run)
    connection.execute(text("INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) VALUES (:id, :symbol, 'TEST', 'ACTIVE')"), {"id": instrument_id, "symbol": job_key.upper()})
    assert SqlAlchemyJobRunRepository(connection).claim(run) is True
    signal, order, fill = _chain(context, instrument_id)
    assert PaperSignalWriter(SqlAlchemyPaperSignalRepository(connection)).record(context, signal) is True
    assert PaperOrderWriter(SqlAlchemyPaperOrderRepository(connection)).record(context, order) is True
    return portfolio_id, run, context, fill


@pytest.mark.integration
def test_paper_fill_accounting_restart_replay_is_idempotent_across_committed_connections() -> None:
    """A process restart must not duplicate a committed fill or its economic effects."""
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"

    with engine.begin() as connection:
        portfolio_id, _, context, fill = _setup(connection, migrations_dir, "paper-accounting-restart-replay")
        writer = PaperFillAccountingWriter(SqlAlchemyPaperFillAccountingRepository(connection))
        assert writer.record(context, portfolio_id, Decimal("1000"), fill, sequence=0) is True

    with engine.begin() as connection:
        writer = PaperFillAccountingWriter(SqlAlchemyPaperFillAccountingRepository(connection))
        assert writer.record(context, portfolio_id, Decimal("1000"), fill, sequence=0) is False
        assert connection.execute(text("SELECT count(*) FROM fills WHERE fill_id=:id"), {"id": fill.fill_id}).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM paper_portfolio_fill_applications WHERE portfolio_id=:portfolio_id AND fill_id=:fill_id"), {"portfolio_id": portfolio_id, "fill_id": fill.fill_id}).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM paper_portfolio_pnl_events WHERE portfolio_id=:portfolio_id AND fill_id=:fill_id"), {"portfolio_id": portfolio_id, "fill_id": fill.fill_id}).scalar_one() == 1
        assert connection.execute(text("SELECT cash, version FROM paper_portfolios WHERE portfolio_id=:id"), {"id": portfolio_id}).one() == (Decimal("799.50"), 1)
        assert connection.execute(text("SELECT quantity, average_price, realized_pnl, total_commission FROM paper_portfolio_positions WHERE portfolio_id=:portfolio_id AND instrument_id=:instrument_id"), {"portfolio_id": portfolio_id, "instrument_id": fill.instrument_id}).one() == (Decimal("2"), Decimal("100"), Decimal("0"), Decimal("0.50"))


@pytest.mark.integration
def test_completed_authoritative_accounting_reconciles_without_runtime_replay() -> None:
    """A committed portfolio-PNL chain can terminalize after process interruption."""
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"

    with engine.begin() as connection:
        portfolio_id, run, context, fill = _setup(
            connection,
            migrations_dir,
            "paper-accounting-reconcile",
            authenticated_job=True,
        )
        assert PaperRiskWriter(SqlAlchemyPaperRiskRepository(connection)).record(
            context,
            RiskAssessment(
                signal_id=fill.signal_id,
                decision=RiskDecision.APPROVE,
                reason_code="TEST_APPROVED",
                approved_quantity=fill.quantity,
            ),
        )
        writer = PaperFillAccountingWriter(SqlAlchemyPaperFillAccountingRepository(connection))
        assert writer.record(context, portfolio_id, Decimal("1000"), fill, sequence=0) is True
        effect_count = connection.execute(
            text("SELECT count(*) FROM paper_effects WHERE job_run_id=:id"),
            {"id": run.job_run_id},
        ).scalar_one()

    with engine.begin() as connection:
        assert reconcile_completed_paper_run(
            connection,
            run,
            current=datetime(2026, 9, 30, 12, 2, tzinfo=UTC),
        ) is True
        record = SqlAlchemyJobRunRepository(connection).get_record(run.job_run_id)
        assert record is not None
        assert record.status is JobRunStatus.SUCCEEDED
        assert connection.execute(
            text("SELECT count(*) FROM paper_effects WHERE job_run_id=:id"),
            {"id": run.job_run_id},
        ).scalar_one() == effect_count
        assert connection.execute(
            text("SELECT cash, version FROM paper_portfolios WHERE portfolio_id=:id"),
            {"id": portfolio_id},
        ).one() == (Decimal("799.50"), 1)
        audit = connection.execute(
            text(
                "SELECT event_type, entity_type, entity_id, payload "
                "FROM audit_events WHERE event_type='PAPER_RUN_RECONCILED' "
                "AND entity_id=:id"
            ),
            {"id": str(run.job_run_id)},
        ).one()
        assert audit.event_type == "PAPER_RUN_RECONCILED"
        assert audit.entity_type == "JOB_RUN"
        assert audit.entity_id == str(run.job_run_id)
        assert audit.payload["decision"] == "ACKNOWLEDGE_COMPLETE_EFFECTS"
        assert audit.payload["schema_version"] == 1
        assert {effect["effect_type"] for effect in audit.payload["effects"]} == {
            "SIGNAL",
            "RISK",
            "ORDER",
            "FILL",
            "PNL",
        }

    engine.dispose()


@pytest.mark.integration
def test_paper_fill_accounting_failed_transaction_leaves_no_restart_visible_partial_state() -> None:
    """A failed atomic fill/accounting attempt must leave no economic residue after restart."""
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"

    with engine.begin() as connection:
        portfolio_id, run, context, fill = _setup(connection, migrations_dir, "paper-accounting-restart-rollback")
        event_id = paper_portfolio_pnl_event_id(portfolio_id, fill.fill_id)
        conflict = create_paper_effect(run, PaperEffectType.PNL, event_id, "0" * 64)
        assert SqlAlchemyPaperEffectRepository(connection).record(conflict) is True
        writer = PaperFillAccountingWriter(SqlAlchemyPaperFillAccountingRepository(connection))
        with pytest.raises(ValueError, match="PAPER_EFFECT_IDENTITY_CONFLICT"):
            writer.record(context, portfolio_id, Decimal("1000"), fill, sequence=0)

    # A new connection models restart after the failed transaction was committed by its caller.
    with engine.begin() as connection:
        assert connection.execute(text("SELECT count(*) FROM fills WHERE fill_id=:id"), {"id": fill.fill_id}).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM paper_effects WHERE effect_type='FILL' AND entity_id=:id"), {"id": fill.fill_id}).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM paper_portfolios WHERE portfolio_id=:id"), {"id": portfolio_id}).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM paper_portfolio_fill_applications WHERE portfolio_id=:id"), {"id": portfolio_id}).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM paper_portfolio_pnl_events WHERE portfolio_id=:id"), {"id": portfolio_id}).scalar_one() == 0
