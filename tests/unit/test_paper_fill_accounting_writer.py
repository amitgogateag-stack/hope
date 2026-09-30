from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper.context import PaperCycleContext
from hope.application.paper.fill_accounting import PaperFillAccountingWriter
from hope.domain.execution import OrderSide
from hope.domain.execution.simulator import Fill


UTC = timezone.utc


class _Repository:
    def __init__(self) -> None:
        self.calls = []

    def persist(self, context, portfolio_id, initial_cash, fill, *, sequence: int) -> bool:
        self.calls.append((context, portfolio_id, initial_cash, fill, sequence))
        return True


def _context() -> PaperCycleContext:
    return PaperCycleContext(
        create_scheduled_job_run(
            "paper-fill-accounting-writer",
            datetime(2026, 9, 30, 20, 0, tzinfo=UTC),
        )
    )


def _fill(context: PaperCycleContext, *, sequence: int = 0) -> Fill:
    signal_id = uuid4()
    return Fill(
        context.fill_id(signal_id, sequence),
        context.order_id(signal_id),
        signal_id,
        uuid4(),
        OrderSide.BUY,
        Decimal("1"),
        Decimal("100"),
        Decimal("0.25"),
        Decimal("0"),
        "paper-accounting-writer-v1",
        datetime(2026, 9, 30, 19, 59, tzinfo=UTC),
    )


def test_paper_fill_accounting_writer_rejects_invalid_portfolio_and_cash_before_persistence() -> None:
    context = _context()
    fill = _fill(context)
    repository = _Repository()
    writer = PaperFillAccountingWriter(repository)

    with pytest.raises(ValueError, match="PAPER_ACCOUNTING_PORTFOLIO_ID_INVALID"):
        writer.record(context, "not-a-uuid", Decimal("1000"), fill, sequence=0)

    for invalid_cash in (Decimal("NaN"), Decimal("Infinity"), Decimal("-1")):
        with pytest.raises(ValueError, match="PAPER_ACCOUNTING_INITIAL_CASH_INVALID"):
            writer.record(context, uuid4(), invalid_cash, fill, sequence=0)

    assert repository.calls == []


def test_paper_fill_accounting_writer_rejects_order_lineage_mismatch_before_persistence() -> None:
    context = _context()
    fill = _fill(context)
    repository = _Repository()
    writer = PaperFillAccountingWriter(repository)
    broken = Fill(
        fill.fill_id,
        uuid4(),
        fill.signal_id,
        fill.instrument_id,
        fill.side,
        fill.quantity,
        fill.price,
        fill.commission,
        fill.slippage,
        fill.cost_model_version,
        fill.fill_time,
    )

    with pytest.raises(ValueError, match="PAPER_FILL_ORDER_IDENTITY_MISMATCH"):
        writer.record(context, uuid4(), Decimal("1000"), broken, sequence=0)

    assert repository.calls == []


def test_paper_fill_accounting_writer_rejects_fill_identity_mismatch_before_persistence() -> None:
    context = _context()
    fill = _fill(context)
    repository = _Repository()
    writer = PaperFillAccountingWriter(repository)
    broken = Fill(
        uuid4(),
        fill.order_id,
        fill.signal_id,
        fill.instrument_id,
        fill.side,
        fill.quantity,
        fill.price,
        fill.commission,
        fill.slippage,
        fill.cost_model_version,
        fill.fill_time,
    )

    with pytest.raises(ValueError, match="PAPER_FILL_IDENTITY_MISMATCH"):
        writer.record(context, uuid4(), Decimal("1000"), broken, sequence=0)

    assert repository.calls == []


def test_paper_fill_accounting_writer_persists_valid_deterministic_fill() -> None:
    context = _context()
    fill = _fill(context, sequence=2)
    portfolio_id = uuid4()
    repository = _Repository()
    writer = PaperFillAccountingWriter(repository)

    assert writer.record(context, portfolio_id, Decimal("1000"), fill, sequence=2) is True
    assert repository.calls == [(context, portfolio_id, Decimal("1000"), fill, 2)]
