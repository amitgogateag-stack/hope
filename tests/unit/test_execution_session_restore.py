from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.domain.execution import CostModel, Environment, ExecutionQuote, Order, OrderSide, simulate_market_fill
from hope.domain.execution.lifecycle import OrderLifecycle
from hope.domain.execution.session import ExecutionSession, ExecutionSessionError
from hope.domain.execution.timeline import ExecutionTimeline
from hope.domain.portfolio.ledger import PortfolioLedger


BASE_TIME = datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc)


def make_order():
    return Order(
        order_id=uuid4(),
        signal_id=uuid4(),
        instrument_id=uuid4(),
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        environment=Environment.PAPER,
    )


def make_fill(order, quantity, minute):
    fill_time = BASE_TIME + timedelta(minutes=minute)
    quote = ExecutionQuote(order.instrument_id, fill_time, Decimal("100"), Decimal("101"))
    timeline = ExecutionTimeline.from_decision(BASE_TIME, latency=timedelta(0)).with_fill_time(fill_time)
    return simulate_market_fill(
        order,
        quote,
        uuid4(),
        CostModel("restore-test"),
        Decimal(quantity),
        timeline=timeline,
    )


def test_session_state_restores_onto_shared_ledger_and_resumes_execution():
    order = make_order()
    source_ledger = PortfolioLedger(Decimal("10000"))
    source_session = ExecutionSession(OrderLifecycle(order), source_ledger, order_time=BASE_TIME)
    first_fill = make_fill(order, "4", 1)
    snapshot = source_session.apply_fill(first_fill)

    restored_ledger = PortfolioLedger.from_state(
        snapshot.portfolio,
        applied_fill_ids=snapshot.fill_ids,
    )
    restored_session = ExecutionSession.from_state(snapshot, restored_ledger)

    assert restored_session.state == snapshot

    resumed = restored_session.apply_fill(make_fill(order, "6", 2))

    assert resumed.lifecycle.status == "FILLED"
    assert resumed.lifecycle.remaining_quantity == Decimal("0")
    assert first_fill.fill_id in resumed.fill_ids
    assert resumed.last_fill_time == BASE_TIME + timedelta(minutes=2)
    assert restored_ledger.state == resumed.portfolio
    assert resumed.portfolio.positions[order.instrument_id].quantity == Decimal("10")


def test_session_restore_rejects_portfolio_baseline_mismatch():
    order = make_order()
    source_ledger = PortfolioLedger(Decimal("10000"))
    source_session = ExecutionSession(OrderLifecycle(order), source_ledger, order_time=BASE_TIME)
    snapshot = source_session.apply_fill(make_fill(order, "4", 1))
    mismatched_ledger = PortfolioLedger(Decimal("10000"))

    with pytest.raises(ExecutionSessionError, match="EXECUTION_SESSION_PORTFOLIO_MISMATCH"):
        ExecutionSession.from_state(snapshot, mismatched_ledger)


def test_session_restore_rejects_fill_history_mismatch():
    order = make_order()
    source_ledger = PortfolioLedger(Decimal("10000"))
    source_session = ExecutionSession(OrderLifecycle(order), source_ledger, order_time=BASE_TIME)
    snapshot = source_session.apply_fill(make_fill(order, "4", 1))
    restored_ledger = PortfolioLedger.from_state(
        snapshot.portfolio,
        applied_fill_ids=snapshot.fill_ids,
    )
    malformed = replace(snapshot, fill_ids=frozenset())

    with pytest.raises(ExecutionSessionError, match="EXECUTION_SESSION_FILL_HISTORY_MISMATCH"):
        ExecutionSession.from_state(malformed, restored_ledger)
