import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper import (
    PaperCycleContext,
    PaperOrderWriter,
    PaperRiskWriter,
    PaperSignalWriter,
)
from hope.domain.execution.models import Environment, Order, OrderSide
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_orders import SqlAlchemyPaperOrderRepository
from hope.infrastructure.repositories.paper_risk import SqlAlchemyPaperRiskRepository
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository


def _setup_signal(connection, migrations_dir):
    apply_migrations(connection, migrations_dir)
    instrument_id = uuid4()
    decision_time = datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc)
    run = create_scheduled_job_run(
        f"paper-order-risk-authorization:{uuid4()}", decision_time
    )
    context = PaperCycleContext(run)
    connection.execute(
        text(
            "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
            "VALUES (:id, :symbol, 'TEST', 'ACTIVE')"
        ),
        {"id": instrument_id, "symbol": f"ORDER-RISK-{instrument_id.hex[:12]}"},
    )
    assert SqlAlchemyJobRunRepository(connection).claim(run) is True
    signal = Signal(
        signal_id=context.signal_id(
            instrument_id=instrument_id,
            strategy_version="paper-order-risk-v1",
            decision_time=decision_time,
            signal_type=SignalType.ENTRY,
            conviction=Decimal("0.8"),
            inputs_hash="c" * 64,
        ),
        instrument_id=instrument_id,
        strategy_version="paper-order-risk-v1",
        decision_time=decision_time,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"),
        inputs_hash="c" * 64,
    )
    assert PaperSignalWriter(
        SqlAlchemyPaperSignalRepository(connection)
    ).record(context, signal) is True
    return context, signal


def _order(context, signal, quantity):
    return Order(
        order_id=context.order_id(signal.signal_id),
        signal_id=signal.signal_id,
        instrument_id=signal.instrument_id,
        side=OrderSide.BUY,
        quantity=quantity,
        environment=Environment.PAPER,
        signal_type=SignalType.ENTRY,
    )


@pytest.mark.integration
def test_matching_paper_risk_authorizes_canonical_order() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"

    with engine.begin() as connection:
        context, signal = _setup_signal(connection, migrations_dir)
        risk = RiskAssessment(
            signal_id=signal.signal_id,
            decision=RiskDecision.APPROVE,
            reason_code="TEST_APPROVED",
            approved_quantity=Decimal("2"),
        )
        assert PaperRiskWriter(
            SqlAlchemyPaperRiskRepository(connection)
        ).record(context, risk) is True
        assert PaperOrderWriter(
            SqlAlchemyPaperOrderRepository(connection)
        ).record(context, _order(context, signal, Decimal("2"))) is True

    engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("decision", "approved_quantity", "error"),
    [
        (RiskDecision.APPROVE, Decimal("2"), "PAPER_ORDER_RISK_QUANTITY_MISMATCH"),
        (RiskDecision.REJECT, Decimal("0"), "PAPER_ORDER_RISK_NOT_APPROVED"),
    ],
)
def test_conflicting_risk_rejects_later_paper_order(
    decision,
    approved_quantity,
    error,
) -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"

    with engine.begin() as connection:
        context, signal = _setup_signal(connection, migrations_dir)
        risk = RiskAssessment(
            signal_id=signal.signal_id,
            decision=decision,
            reason_code="TEST_DECISION",
            approved_quantity=approved_quantity,
        )
        assert PaperRiskWriter(
            SqlAlchemyPaperRiskRepository(connection)
        ).record(context, risk) is True
        order = _order(context, signal, Decimal("1"))
        with pytest.raises(IntegrityError, match=error):
            PaperOrderWriter(
                SqlAlchemyPaperOrderRepository(connection)
            ).record(context, order)
        assert connection.execute(
            text("SELECT count(*) FROM orders WHERE order_id=:order_id"),
            {"order_id": order.order_id},
        ).scalar_one() == 0

    engine.dispose()


@pytest.mark.integration
def test_conflicting_order_rejects_later_risk_and_rolls_back_risk_effect() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"

    with engine.begin() as connection:
        context, signal = _setup_signal(connection, migrations_dir)
        order = _order(context, signal, Decimal("1"))
        connection.execute(
            text(
                "INSERT INTO orders(order_id, signal_id, instrument_id, environment, "
                "side, quantity, signal_type) VALUES ("
                ":order_id, :signal_id, :instrument_id, 'PAPER', 'BUY', 1, 'ENTRY')"
            ),
            {
                "order_id": order.order_id,
                "signal_id": order.signal_id,
                "instrument_id": order.instrument_id,
            },
        )
        risk = RiskAssessment(
            signal_id=signal.signal_id,
            decision=RiskDecision.APPROVE,
            reason_code="TEST_APPROVED",
            approved_quantity=Decimal("2"),
        )
        with pytest.raises(
            IntegrityError,
            match="PAPER_ORDER_RISK_QUANTITY_MISMATCH",
        ):
            PaperRiskWriter(
                SqlAlchemyPaperRiskRepository(connection)
            ).record(context, risk)
        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_effects "
                "WHERE effect_type='RISK' AND entity_id=:signal_id"
            ),
            {"signal_id": signal.signal_id},
        ).scalar_one() == 0
        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_risk_assessments "
                "WHERE signal_id=:signal_id"
            ),
            {"signal_id": signal.signal_id},
        ).scalar_one() == 0

    engine.dispose()
