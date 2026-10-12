"""Reconciliation receipts must not survive a failed authenticated reread."""

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from hope.application.jobs import (
    JobRunStatus,
    create_job_run_completion,
    create_scheduled_job_run,
)
from hope.application.paper import (
    PaperCycleContext,
    PaperOrderWriter,
    PaperRiskWriter,
    PaperSignalWriter,
)
from hope.application.paper.terminals import PaperTerminalWriter
from hope.domain.execution.models import (
    Environment,
    ExecutionRejection,
    Order,
    OrderSide,
)
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository
from hope.infrastructure.repositories.paper_orders import SqlAlchemyPaperOrderRepository
from hope.infrastructure.repositories.paper_risk import SqlAlchemyPaperRiskRepository
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository
from hope.infrastructure.repositories.paper_terminals import SqlAlchemyPaperTerminalRepository
from hope.infrastructure.repositories.paper_reconciliation_audit import (
    SqlAlchemyPaperReconciliationAuditRepository,
)


@pytest.mark.integration
def test_failed_receipt_reread_rolls_back_insert_even_when_caught(monkeypatch) -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    strategy_id, version_id = uuid4(), uuid4()
    scheduled_for = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    run = create_scheduled_job_run(
        f"paper:USA:{version_id}:receipt-savepoint",
        scheduled_for,
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text(
                "INSERT INTO strategies(strategy_id, name, family) "
                "VALUES (:id, :name, 'TEST')"
            ),
            {"id": strategy_id, "name": f"PAPER_RECEIPT_{strategy_id}"},
        )
        connection.execute(
            text(
                "INSERT INTO strategy_versions("
                "strategy_version_id, strategy_id, version, code_commit"
                ") VALUES (:version_id, :strategy_id, 'v1', 'receipt-savepoint-test')"
            ),
            {"version_id": version_id, "strategy_id": strategy_id},
        )
        jobs = SqlAlchemyJobRunRepository(connection)
        assert jobs.claim(run) is True

        instrument_id = uuid4()
        decision_time = scheduled_for - timedelta(minutes=10)
        connection.execute(
            text(
                "INSERT INTO instruments("
                "instrument_id, canonical_symbol, exchange, status"
                ") VALUES (:id, :symbol, 'TEST', 'ACTIVE')"
            ),
            {"id": instrument_id, "symbol": f"RECEIPT-{instrument_id}"},
        )
        context = PaperCycleContext(run)
        signal_id = context.signal_id(
            instrument_id=instrument_id,
            strategy_version="receipt-savepoint-v1",
            decision_time=decision_time,
            signal_type=SignalType.ENTRY,
            conviction=Decimal("0.8"),
            inputs_hash="f" * 64,
        )
        signal = Signal(
            signal_id=signal_id,
            instrument_id=instrument_id,
            strategy_version="receipt-savepoint-v1",
            decision_time=decision_time,
            signal_type=SignalType.ENTRY,
            conviction=Decimal("0.8"),
            inputs_hash="f" * 64,
        )
        order = Order(
            order_id=context.order_id(signal_id),
            signal_id=signal_id,
            instrument_id=instrument_id,
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            environment=Environment.PAPER,
            signal_type=SignalType.ENTRY,
        )
        assert PaperSignalWriter(
            SqlAlchemyPaperSignalRepository(connection)
        ).record(context, signal)
        assert PaperRiskWriter(SqlAlchemyPaperRiskRepository(connection)).record(
            context,
            RiskAssessment(
                signal_id=signal_id,
                decision=RiskDecision.APPROVE,
                reason_code="TEST_APPROVED",
                approved_quantity=Decimal("1"),
            ),
        )
        assert PaperOrderWriter(SqlAlchemyPaperOrderRepository(connection)).record(
            context, order
        )
        assert PaperTerminalWriter(
            SqlAlchemyPaperTerminalRepository(connection)
        ).record(
            context,
            ExecutionRejection(
                order.order_id,
                signal_id,
                instrument_id,
                Environment.PAPER,
                "TEST_REJECT",
                scheduled_for - timedelta(minutes=5),
            ),
        )

        effects_repository = SqlAlchemyPaperEffectRepository(connection)
        assert jobs.complete(
            create_job_run_completion(
                run, JobRunStatus.SUCCEEDED, scheduled_for + timedelta(minutes=2)
            )
        ) is True
        completion = jobs.get_record_for_run(run)
        assert completion is not None
        effects = effects_repository.list_for_job_run(run.job_run_id)

        audit = SqlAlchemyPaperReconciliationAuditRepository(connection)
        with monkeypatch.context() as patch:
            patch.setattr(audit, "verify", lambda *args: False)
            with pytest.raises(
                RuntimeError, match="PAPER_RECONCILIATION_AUDIT_NOT_DURABLE"
            ):
                audit.record(completion, effects)

        # The caller caught the error but did not roll back its outer transaction.
        # The rejected receipt must not be visible or committed.
        assert connection.execute(
            text(
                "SELECT count(*) FROM audit_events "
                "WHERE event_type='PAPER_RUN_RECONCILED' AND entity_id=:id"
            ),
            {"id": str(run.job_run_id)},
        ).scalar_one() == 0

        assert audit.record(completion, effects) is True
        assert audit.verify(completion, effects) is True

    with engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT count(*) FROM audit_events "
                "WHERE event_type='PAPER_RUN_RECONCILED' AND entity_id=:id"
            ),
            {"id": str(run.job_run_id)},
        ).scalar_one() == 1
    engine.dispose()
