from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper import PaperCycleContext
from hope.application.paper.orders import PaperOrderWriter, paper_order_payload_hash
from hope.domain.execution.models import Environment, Order, OrderSide
from hope.domain.signal.models import SignalType


UTC = timezone.utc


class FakePaperOrderPersistence:
    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.calls = []

    def persist(self, effect, order) -> bool:
        self.calls.append((effect, order))
        return self.result


def make_context(minute: int = 0) -> PaperCycleContext:
    return PaperCycleContext(
        create_scheduled_job_run(
            "paper-order-writer",
            datetime(2026, 9, 9, 23, minute, tzinfo=UTC),
        )
    )


def make_order(
    context: PaperCycleContext,
    *,
    signal_id=None,
    instrument_id=None,
    quantity: Decimal = Decimal("2"),
    environment: Environment = Environment.PAPER,
) -> Order:
    signal = signal_id or uuid4()
    return Order(
        order_id=context.order_id(signal),
        signal_id=signal,
        instrument_id=instrument_id or uuid4(),
        side=OrderSide.BUY,
        quantity=quantity,
        environment=environment,
        signal_type=SignalType.ENTRY,
    )


def test_paper_order_writer_records_order_effect_with_job_lineage() -> None:
    context = make_context()
    order = make_order(context)
    repository = FakePaperOrderPersistence()

    assert PaperOrderWriter(repository).record(context, order) is True
    assert len(repository.calls) == 1
    effect, persisted_order = repository.calls[0]
    assert persisted_order == order
    assert effect.job_run_id == context.job_run.job_run_id
    assert effect.effect_type.value == "ORDER"
    assert effect.entity_id == order.order_id
    assert effect.payload_hash == paper_order_payload_hash(order)


def test_paper_order_writer_preserves_repository_duplicate_result() -> None:
    context = make_context()
    order = make_order(context)
    repository = FakePaperOrderPersistence(result=False)

    assert PaperOrderWriter(repository).record(context, order) is False
    assert len(repository.calls) == 1


def test_paper_order_writer_rejects_non_deterministic_order_id() -> None:
    context = make_context()
    valid = make_order(context)
    invalid = valid.model_copy(update={"order_id": uuid4()})
    repository = FakePaperOrderPersistence()

    with pytest.raises(ValueError, match="PAPER_ORDER_IDENTITY_MISMATCH"):
        PaperOrderWriter(repository).record(context, invalid)
    assert repository.calls == []


def test_paper_order_writer_rejects_non_paper_environment() -> None:
    context = make_context()
    order = make_order(context, environment=Environment.BACKTEST)
    repository = FakePaperOrderPersistence()

    with pytest.raises(ValueError, match="PAPER_ORDER_REQUIRES_PAPER_ENVIRONMENT"):
        PaperOrderWriter(repository).record(context, order)
    assert repository.calls == []


def test_paper_order_payload_hash_canonicalizes_equivalent_quantity() -> None:
    context = make_context()
    signal_id = uuid4()
    instrument_id = uuid4()
    first = make_order(
        context,
        signal_id=signal_id,
        instrument_id=instrument_id,
        quantity=Decimal("2.0"),
    )
    second = make_order(
        context,
        signal_id=signal_id,
        instrument_id=instrument_id,
        quantity=Decimal("2.000"),
    )

    assert first.order_id == second.order_id
    assert paper_order_payload_hash(first) == paper_order_payload_hash(second)
