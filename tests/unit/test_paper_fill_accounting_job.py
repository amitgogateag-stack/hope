from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper.context import PaperCycleContext
from hope.application.paper.jobs import PaperFillAccountingJob
from hope.domain.execution import OrderSide
from hope.domain.execution.simulator import Fill


UTC = timezone.utc


class _Runtime:
    def __init__(self, job_run) -> None:
        self.cycle = PaperCycleContext(job_run)
        self.recorded = []

    def record_fill(self, portfolio_id, initial_cash, fill, *, sequence: int) -> bool:
        self.recorded.append((portfolio_id, initial_cash, fill, sequence))
        return True


def _fill(fill_time: datetime | None) -> Fill:
    return Fill(
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        OrderSide.BUY,
        Decimal("1"),
        Decimal("100"),
        Decimal("0.25"),
        Decimal("0"),
        "paper-fill-job-v1",
        fill_time,
    )


def test_paper_fill_accounting_job_requires_typed_inputs() -> None:
    with pytest.raises(TypeError, match="PAPER_FILL_JOB_REQUIRES_PORTFOLIO_ID"):
        PaperFillAccountingJob(object(), Decimal("1000"), _fill(datetime.now(UTC)))
    with pytest.raises(TypeError, match="PAPER_FILL_JOB_REQUIRES_DECIMAL_INITIAL_CASH"):
        PaperFillAccountingJob(uuid4(), 1000, _fill(datetime.now(UTC)))
    with pytest.raises(TypeError, match="PAPER_FILL_JOB_REQUIRES_FILL"):
        PaperFillAccountingJob(uuid4(), Decimal("1000"), object())


def test_paper_fill_accounting_job_rejects_invalid_cash_and_sequence() -> None:
    fill = _fill(datetime.now(UTC))
    with pytest.raises(ValueError, match="PAPER_FILL_JOB_INITIAL_CASH_INVALID"):
        PaperFillAccountingJob(uuid4(), Decimal("NaN"), fill)
    with pytest.raises(ValueError, match="PAPER_FILL_JOB_INITIAL_CASH_INVALID"):
        PaperFillAccountingJob(uuid4(), Decimal("-1"), fill)
    with pytest.raises(ValueError, match="PAPER_FILL_JOB_SEQUENCE_INVALID"):
        PaperFillAccountingJob(uuid4(), Decimal("1000"), fill, sequence=-1)


def test_paper_fill_accounting_job_records_fill_and_accounting() -> None:
    scheduled_for = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    job_run = create_scheduled_job_run("paper-fill-account", scheduled_for)
    portfolio_id = uuid4()
    fill = _fill(datetime(2026, 9, 10, 13, 59, tzinfo=UTC))
    runtime = _Runtime(job_run)

    PaperFillAccountingJob(portfolio_id, Decimal("1000"), fill, sequence=2)(runtime)

    assert runtime.recorded == [(portfolio_id, Decimal("1000"), fill, 2)]


def test_paper_fill_accounting_job_rejects_missing_or_naive_fill_time() -> None:
    scheduled_for = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    job_run = create_scheduled_job_run("paper-fill-account", scheduled_for)
    runtime = _Runtime(job_run)

    for fill_time in (None, datetime(2026, 9, 10, 13, 59)):
        with pytest.raises(ValueError, match="PAPER_FILL_TIME_MUST_BE_TIMEZONE_AWARE"):
            PaperFillAccountingJob(uuid4(), Decimal("1000"), _fill(fill_time))(runtime)

    assert runtime.recorded == []


def test_paper_fill_accounting_job_rejects_future_fill() -> None:
    scheduled_for = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    job_run = create_scheduled_job_run("paper-fill-account", scheduled_for)
    runtime = _Runtime(job_run)

    with pytest.raises(ValueError, match="PAPER_FILL_AFTER_JOB_SCHEDULE"):
        PaperFillAccountingJob(
            uuid4(),
            Decimal("1000"),
            _fill(datetime(2026, 9, 10, 14, 0, 1, tzinfo=UTC)),
        )(runtime)

    assert runtime.recorded == []
