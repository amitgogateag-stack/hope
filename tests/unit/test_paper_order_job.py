from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper.context import PaperCycleContext
from hope.application.paper.jobs import PaperOrderPersistenceJob
from hope.domain.execution.models import Environment, Order, OrderSide
from hope.domain.signal.models import SignalType


UTC = timezone.utc


class _Runtime:
    def __init__(self, job_run) -> None:
        self.cycle = PaperCycleContext(job_run)
        self.recorded = []

    def record_order(self, order: Order) -> bool:
        self.recorded.append(order)
        return True


def _order(job_run, *, environment: Environment = Environment.PAPER) -> Order:
    signal_id = uuid4()
    context = PaperCycleContext(job_run)
    return Order(
        order_id=context.order_id(signal_id),
        signal_id=signal_id,
        instrument_id=uuid4(),
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        environment=environment,
        signal_type=SignalType.ENTRY,
    )


def test_paper_order_persistence_job_requires_order_model() -> None:
    with pytest.raises(TypeError, match="PAPER_ORDER_JOB_REQUIRES_ORDER"):
        PaperOrderPersistenceJob(object())


def test_paper_order_persistence_job_requires_paper_environment() -> None:
    job_run = create_scheduled_job_run(
        "paper-order-persist",
        datetime(2026, 9, 10, 14, 0, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="PAPER_ORDER_JOB_REQUIRES_PAPER_ENVIRONMENT"):
        PaperOrderPersistenceJob(_order(job_run, environment=Environment.BACKTEST))


def test_paper_order_persistence_job_records_decided_order() -> None:
    job_run = create_scheduled_job_run(
        "paper-order-persist",
        datetime(2026, 9, 10, 14, 0, tzinfo=UTC),
    )
    order = _order(job_run)
    runtime = _Runtime(job_run)

    PaperOrderPersistenceJob(order)(runtime)

    assert runtime.recorded == [order]
