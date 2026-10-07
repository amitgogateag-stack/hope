from contextlib import nullcontext
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from hope.application.paper.portfolio_pnl import (
    PaperPortfolioPnLEvent,
    paper_portfolio_pnl_event_id,
)
from hope.infrastructure.repositories.paper_accounting import (
    SqlAlchemyPaperAccountingRepository,
)


def _context():
    return SimpleNamespace(job_run=SimpleNamespace(job_run_id=uuid4()))


def _retry_boundary(*, realized: str = "4", commission: str = "0.50"):
    portfolio_id = uuid4()
    fill = SimpleNamespace(
        fill_id=uuid4(),
        instrument_id=uuid4(),
        fill_time=datetime(2026, 10, 7, 1, 0, tzinfo=timezone.utc),
    )
    event = PaperPortfolioPnLEvent(
        pnl_event_id=paper_portfolio_pnl_event_id(portfolio_id, fill.fill_id),
        portfolio_id=portfolio_id,
        fill_id=fill.fill_id,
        instrument_id=fill.instrument_id,
        realized_pnl_delta=Decimal(realized),
        commission_delta=Decimal(commission),
        event_time=fill.fill_time,
    )
    replayed = SimpleNamespace(
        fill_id=fill.fill_id,
        instrument_id=fill.instrument_id,
        realized_pnl_delta=Decimal("4"),
        commission_delta=Decimal("0.50"),
    )
    return portfolio_id, fill, event, replayed


def _repository(event, replayed, *, pnl_recorded: bool = False):
    portfolio = SimpleNamespace(
        apply_fill_with_transition=lambda *args, **kwargs: None,
        load_fill_transition=lambda *args, **kwargs: replayed,
    )
    pnl_calls = []

    def record(*args):
        pnl_calls.append(args)
        return pnl_recorded

    repository = object.__new__(SqlAlchemyPaperAccountingRepository)
    repository._connection = SimpleNamespace(begin_nested=nullcontext)
    repository._effects = SimpleNamespace(
        get_reusable_for_job=lambda *args: SimpleNamespace()
    )
    repository._portfolio = portfolio
    repository._pnl = SimpleNamespace(get=lambda *args: event)
    repository._pnl_writer = SimpleNamespace(record=record)
    return repository, portfolio, pnl_calls


def test_idempotent_retry_authenticates_replayed_transition() -> None:
    portfolio_id, fill, event, replayed = _retry_boundary()
    calls = []

    def load_fill_transition(*args, **kwargs):
        calls.append((args, kwargs))
        return replayed

    repository, portfolio, pnl_calls = _repository(event, replayed)
    portfolio.load_fill_transition = load_fill_transition

    context = _context()
    assert repository.apply_fill(
        context,
        portfolio_id,
        Decimal("1000"),
        fill,
    ) is False
    assert calls == [
        (
            (portfolio_id, fill.fill_id),
            {
                "lock_for_update": True,
                "current_job_run_id": context.job_run.job_run_id,
            },
        ),
    ]
    assert pnl_calls == [(context, portfolio_id, fill, replayed)]


@pytest.mark.parametrize(
    ("realized", "commission"),
    [
        ("5", "0.50"),
        ("4", "0.75"),
    ],
)
def test_idempotent_retry_rejects_conflicting_pnl_economics(
    realized: str,
    commission: str,
) -> None:
    portfolio_id, fill, event, replayed = _retry_boundary(
        realized=realized,
        commission=commission,
    )
    repository, _, _ = _repository(event, replayed)

    with pytest.raises(
        ValueError,
        match="PAPER_ACCOUNTING_PNL_DURABLE_STATE_CONFLICT",
    ):
        repository.apply_fill(_context(), portfolio_id, Decimal("1000"), fill)


def test_idempotent_retry_requires_replayable_fill_lineage() -> None:
    portfolio_id, fill, event, _ = _retry_boundary()
    repository, _, _ = _repository(event, None)

    with pytest.raises(
        RuntimeError,
        match="PAPER_ACCOUNTING_APPLIED_FILL_TRANSITION_NOT_DURABLE",
    ):
        repository.apply_fill(_context(), portfolio_id, Decimal("1000"), fill)


def test_idempotent_retry_never_recreates_missing_pnl() -> None:
    portfolio_id, fill, event, replayed = _retry_boundary()
    repository, _, _ = _repository(event, replayed, pnl_recorded=True)

    with pytest.raises(
        RuntimeError,
        match="PAPER_ACCOUNTING_RETRY_RECREATED_PNL",
    ):
        repository.apply_fill(_context(), portfolio_id, Decimal("1000"), fill)
