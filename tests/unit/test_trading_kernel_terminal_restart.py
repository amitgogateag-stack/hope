from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from hope.application.trading.service import TradingKernel
from hope.domain.execution.models import Environment, OrderSide
from hope.domain.execution.replay import replay_order
from hope.domain.execution.simulator import CostModel, ExecutionQuote, Fill
from hope.domain.execution.timeline import ExecutionTimeline
from hope.domain.portfolio.ledger import PortfolioLedger
from hope.domain.signal.models import SignalType
from hope.domain.trading.kernel import OrderIntent, materialize_order


UTC = timezone.utc
INSTRUMENT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SIGNAL_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
ORDER_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
FILL_ID = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
NEW_FILL_ID = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")


def make_replayed_entry(fill_quantity: str):
    decision = datetime(2026, 1, 6, 14, 30, tzinfo=UTC)
    intent = OrderIntent(
        signal_id=SIGNAL_ID,
        instrument_id=INSTRUMENT_ID,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        environment=Environment.BACKTEST,
        signal_type=SignalType.ENTRY,
    )
    order = materialize_order(intent, ORDER_ID)
    fill = Fill(
        fill_id=FILL_ID,
        order_id=ORDER_ID,
        signal_id=SIGNAL_ID,
        instrument_id=INSTRUMENT_ID,
        side=OrderSide.BUY,
        quantity=Decimal(fill_quantity),
        price=Decimal("100"),
        commission=Decimal("0"),
        slippage=Decimal("0"),
        cost_model_version="terminal-restart-seed",
        fill_time=decision,
    )
    replayed = replay_order(
        order,
        [fill],
        order_time=decision,
        initial_cash=Decimal("10000"),
    )
    ledger = PortfolioLedger.from_state(
        replayed.state.portfolio,
        applied_fill_ids=replayed.state.fill_ids,
    )
    return decision, intent, replayed.state, ledger


def make_resume_inputs(decision: datetime):
    fill_time = decision + timedelta(minutes=1)
    timeline = ExecutionTimeline.from_decision(
        decision,
        latency=timedelta(0),
    ).with_fill_time(fill_time)
    quote = ExecutionQuote(
        INSTRUMENT_ID,
        fill_time,
        Decimal("100"),
        Decimal("100"),
    )
    return quote, timeline


def test_kernel_restores_terminal_identity_without_registering_resumable_session():
    decision, intent, terminal_state, ledger = make_replayed_entry("10")
    assert terminal_state.lifecycle.status == "FILLED"

    kernel = TradingKernel(ledger)
    restored = kernel.restore_terminal_order(terminal_state)

    assert restored == terminal_state
    assert ORDER_ID not in kernel._execution_sessions
    assert kernel._terminal_orders[ORDER_ID] == terminal_state.lifecycle.order

    quote, timeline = make_resume_inputs(decision)
    with pytest.raises(ValueError, match="TERMINAL_ORDER_ID_CANNOT_BE_REUSED"):
        kernel.execute_order(
            intent,
            ORDER_ID,
            quote,
            CostModel(version="terminal-reuse-test"),
            decision_time=decision,
            fill_id=NEW_FILL_ID,
            timeline=timeline,
        )

    assert ledger.state == terminal_state.portfolio


def test_kernel_resumable_restore_rejects_terminal_state():
    _, _, terminal_state, ledger = make_replayed_entry("10")
    kernel = TradingKernel(ledger)

    with pytest.raises(ValueError, match="EXECUTION_SESSION_NOT_RESUMABLE"):
        kernel.restore_execution_session(terminal_state)

    assert ORDER_ID not in kernel._execution_sessions
    assert ORDER_ID not in kernel._terminal_orders


def test_kernel_terminal_restore_rejects_non_terminal_state():
    _, _, partial_state, ledger = make_replayed_entry("4")
    assert partial_state.lifecycle.status == "PARTIALLY_FILLED"
    kernel = TradingKernel(ledger)

    with pytest.raises(ValueError, match="TERMINAL_ORDER_STATE_REQUIRED"):
        kernel.restore_terminal_order(partial_state)

    assert ORDER_ID not in kernel._terminal_orders


def test_restored_terminal_order_id_rejects_different_intent_identity():
    decision, _, terminal_state, ledger = make_replayed_entry("10")
    kernel = TradingKernel(ledger)
    kernel.restore_terminal_order(terminal_state)

    conflicting_intent = OrderIntent(
        signal_id=SIGNAL_ID,
        instrument_id=INSTRUMENT_ID,
        side=OrderSide.BUY,
        quantity=Decimal("9"),
        environment=Environment.BACKTEST,
        signal_type=SignalType.ENTRY,
    )
    quote, timeline = make_resume_inputs(decision)

    with pytest.raises(ValueError, match="ORDER_ID_REUSED_WITH_DIFFERENT_INTENT"):
        kernel.execute_order(
            conflicting_intent,
            ORDER_ID,
            quote,
            CostModel(version="terminal-conflict-test"),
            decision_time=decision,
            fill_id=NEW_FILL_ID,
            timeline=timeline,
        )

    assert ledger.state == terminal_state.portfolio
