from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from hope.application.trading.service import TradingKernel
from hope.domain.execution.lifecycle import OrderLifecycle
from hope.domain.execution.models import Environment, OrderSide
from hope.domain.execution.replay import replay_order
from hope.domain.execution.session import ExecutionSession
from hope.domain.execution.simulator import CostModel, ExecutionQuote, Fill
from hope.domain.execution.timeline import ExecutionTimeline
from hope.domain.portfolio.ledger import PortfolioLedger
from hope.domain.signal.models import SignalType
from hope.domain.trading.kernel import OrderIntent, materialize_order


UTC = timezone.utc
INSTRUMENT_ID = UUID("11111111-1111-1111-1111-111111111111")
ENTRY_SIGNAL_ID = UUID("22222222-2222-2222-2222-222222222222")
ENTRY_ORDER_ID = UUID("33333333-3333-3333-3333-333333333333")
ENTRY_FILL_ID = UUID("44444444-4444-4444-4444-444444444444")
EXIT_SIGNAL_ID = UUID("55555555-5555-5555-5555-555555555555")
EXIT_ORDER_ID = UUID("66666666-6666-6666-6666-666666666666")
EXIT_FILL_ID = UUID("77777777-7777-7777-7777-777777777777")
RESUME_FILL_ID = UUID("88888888-8888-8888-8888-888888888888")
SECOND_INSTRUMENT_ID = UUID("99999999-9999-9999-9999-999999999999")
SECOND_SIGNAL_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SECOND_ORDER_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
SECOND_FILL_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
SECOND_RESUME_FILL_ID = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")


def make_replayed_partial_entry():
    decision = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    intent = OrderIntent(
        signal_id=ENTRY_SIGNAL_ID,
        instrument_id=INSTRUMENT_ID,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        environment=Environment.BACKTEST,
        signal_type=SignalType.ENTRY,
    )
    order = materialize_order(intent, ENTRY_ORDER_ID)
    first_fill = Fill(
        fill_id=ENTRY_FILL_ID,
        order_id=ENTRY_ORDER_ID,
        signal_id=ENTRY_SIGNAL_ID,
        instrument_id=INSTRUMENT_ID,
        side=OrderSide.BUY,
        quantity=Decimal("4"),
        price=Decimal("100"),
        commission=Decimal("0"),
        slippage=Decimal("0"),
        cost_model_version="seed",
        fill_time=decision,
    )
    replayed = replay_order(
        order,
        [first_fill],
        order_time=decision,
        initial_cash=Decimal("10000"),
    )
    ledger = PortfolioLedger.from_state(
        replayed.state.portfolio,
        applied_fill_ids=replayed.state.fill_ids,
    )
    return decision, intent, replayed.state, ledger


def test_fresh_kernel_exit_intent_cannot_reverse_existing_position():
    decision = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    ledger = PortfolioLedger(Decimal("10000"))
    ledger.apply_fill(
        Fill(
            fill_id=ENTRY_FILL_ID,
            order_id=ENTRY_ORDER_ID,
            signal_id=ENTRY_SIGNAL_ID,
            instrument_id=INSTRUMENT_ID,
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            price=Decimal("100"),
            commission=Decimal("0"),
            slippage=Decimal("0"),
            cost_model_version="seed",
            fill_time=decision,
        )
    )

    exit_intent = OrderIntent(
        signal_id=EXIT_SIGNAL_ID,
        instrument_id=INSTRUMENT_ID,
        side=OrderSide.SELL,
        quantity=Decimal("2"),
        environment=Environment.BACKTEST,
        signal_type=SignalType.EXIT,
    )
    fill_time = decision + timedelta(minutes=1)
    timeline = ExecutionTimeline.from_decision(
        decision,
        latency=timedelta(0),
    ).with_fill_time(fill_time)

    fresh_kernel = TradingKernel(ledger)
    with pytest.raises(ValueError, match="EXIT_QUANTITY_EXCEEDS_POSITION"):
        fresh_kernel.execute_order(
            exit_intent,
            EXIT_ORDER_ID,
            ExecutionQuote(
                INSTRUMENT_ID,
                fill_time,
                Decimal("101"),
                Decimal("101"),
            ),
            CostModel(version="test"),
            decision_time=decision,
            fill_id=EXIT_FILL_ID,
            timeline=timeline,
        )

    assert ledger.state.positions[INSTRUMENT_ID].quantity == Decimal("1")
    assert ledger.state.cash == Decimal("9900")


def test_materialized_order_retains_signal_type_as_durable_identity():
    exit_intent = OrderIntent(
        signal_id=EXIT_SIGNAL_ID,
        instrument_id=INSTRUMENT_ID,
        side=OrderSide.SELL,
        quantity=Decimal("1"),
        environment=Environment.BACKTEST,
        signal_type=SignalType.EXIT,
    )
    entry_intent = OrderIntent(
        signal_id=EXIT_SIGNAL_ID,
        instrument_id=INSTRUMENT_ID,
        side=OrderSide.SELL,
        quantity=Decimal("1"),
        environment=Environment.BACKTEST,
        signal_type=SignalType.ENTRY,
    )

    exit_order = materialize_order(exit_intent, EXIT_ORDER_ID)
    entry_order = materialize_order(entry_intent, EXIT_ORDER_ID)

    assert exit_order.signal_type is SignalType.EXIT
    assert entry_order.signal_type is SignalType.ENTRY
    assert exit_order != entry_order


def test_kernel_restores_partial_execution_session_and_resumes_on_shared_ledger():
    decision, intent, session_state, ledger = make_replayed_partial_entry()
    kernel = TradingKernel(ledger)

    restored = kernel.restore_execution_session(session_state)
    assert restored == session_state

    resume_time = decision + timedelta(minutes=1)
    timeline = ExecutionTimeline.from_decision(
        decision,
        latency=timedelta(0),
    ).with_fill_time(resume_time)
    result = kernel.execute_order(
        intent,
        ENTRY_ORDER_ID,
        ExecutionQuote(
            INSTRUMENT_ID,
            resume_time,
            Decimal("100"),
            Decimal("100"),
        ),
        CostModel(version="resume-test"),
        decision_time=decision,
        fill_id=RESUME_FILL_ID,
        timeline=timeline,
        quantity=Decimal("6"),
    )

    assert result.portfolio_state == ledger.state
    assert ledger.state.positions[INSTRUMENT_ID].quantity == Decimal("10")
    assert ledger.state.cash == Decimal("9000")
    assert kernel._execution_sessions[ENTRY_ORDER_ID].state.lifecycle.status == "FILLED"
    assert kernel._execution_sessions[ENTRY_ORDER_ID].state.fill_ids == frozenset(
        {ENTRY_FILL_ID, RESUME_FILL_ID}
    )


def test_kernel_restart_restore_cannot_overwrite_existing_execution_session():
    _, _, session_state, ledger = make_replayed_partial_entry()
    kernel = TradingKernel(ledger)
    kernel.restore_execution_session(session_state)
    before = ledger.state

    with pytest.raises(ValueError, match="EXECUTION_SESSION_ALREADY_REGISTERED"):
        kernel.restore_execution_session(session_state)

    assert ledger.state == before


def test_kernel_restores_multiple_open_orders_on_one_shared_portfolio_baseline():
    decision = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    first_intent = OrderIntent(
        signal_id=ENTRY_SIGNAL_ID,
        instrument_id=INSTRUMENT_ID,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        environment=Environment.BACKTEST,
        signal_type=SignalType.ENTRY,
    )
    second_intent = OrderIntent(
        signal_id=SECOND_SIGNAL_ID,
        instrument_id=SECOND_INSTRUMENT_ID,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        environment=Environment.BACKTEST,
        signal_type=SignalType.ENTRY,
    )
    first_order = materialize_order(first_intent, ENTRY_ORDER_ID)
    second_order = materialize_order(second_intent, SECOND_ORDER_ID)

    source_ledger = PortfolioLedger(Decimal("20000"))
    first_session = ExecutionSession(
        OrderLifecycle(first_order), source_ledger, order_time=decision
    )
    second_session = ExecutionSession(
        OrderLifecycle(second_order), source_ledger, order_time=decision
    )
    first_session.apply_fill(
        Fill(
            fill_id=ENTRY_FILL_ID,
            order_id=ENTRY_ORDER_ID,
            signal_id=ENTRY_SIGNAL_ID,
            instrument_id=INSTRUMENT_ID,
            side=OrderSide.BUY,
            quantity=Decimal("4"),
            price=Decimal("100"),
            commission=Decimal("0"),
            slippage=Decimal("0"),
            cost_model_version="seed",
            fill_time=decision,
        )
    )
    second_session.apply_fill(
        Fill(
            fill_id=SECOND_FILL_ID,
            order_id=SECOND_ORDER_ID,
            signal_id=SECOND_SIGNAL_ID,
            instrument_id=SECOND_INSTRUMENT_ID,
            side=OrderSide.BUY,
            quantity=Decimal("3"),
            price=Decimal("200"),
            commission=Decimal("0"),
            slippage=Decimal("0"),
            cost_model_version="seed",
            fill_time=decision + timedelta(minutes=1),
        )
    )

    first_shutdown = first_session.state
    second_shutdown = second_session.state
    assert first_shutdown.portfolio == second_shutdown.portfolio

    restored_ledger = PortfolioLedger.from_state(
        first_shutdown.portfolio,
        applied_fill_ids={ENTRY_FILL_ID, SECOND_FILL_ID},
    )
    kernel = TradingKernel(restored_ledger)
    kernel.restore_execution_session(first_shutdown)
    kernel.restore_execution_session(second_shutdown)

    first_resume_time = decision + timedelta(minutes=2)
    first_result = kernel.execute_order(
        first_intent,
        ENTRY_ORDER_ID,
        ExecutionQuote(
            INSTRUMENT_ID,
            first_resume_time,
            Decimal("100"),
            Decimal("100"),
        ),
        CostModel(version="resume-test"),
        decision_time=decision,
        fill_id=RESUME_FILL_ID,
        timeline=ExecutionTimeline.from_decision(
            decision, latency=timedelta(0)
        ).with_fill_time(first_resume_time),
        quantity=Decimal("6"),
    )
    assert first_result.portfolio_state == restored_ledger.state

    second_resume_time = decision + timedelta(minutes=3)
    second_result = kernel.execute_order(
        second_intent,
        SECOND_ORDER_ID,
        ExecutionQuote(
            SECOND_INSTRUMENT_ID,
            second_resume_time,
            Decimal("200"),
            Decimal("200"),
        ),
        CostModel(version="resume-test"),
        decision_time=decision,
        fill_id=SECOND_RESUME_FILL_ID,
        timeline=ExecutionTimeline.from_decision(
            decision, latency=timedelta(0)
        ).with_fill_time(second_resume_time),
        quantity=Decimal("7"),
    )

    assert second_result.portfolio_state == restored_ledger.state
    assert restored_ledger.state.cash == Decimal("17000")
    assert restored_ledger.state.positions[INSTRUMENT_ID].quantity == Decimal("10")
    assert restored_ledger.state.positions[SECOND_INSTRUMENT_ID].quantity == Decimal("10")
    assert restored_ledger.applied_fill_ids == frozenset(
        {ENTRY_FILL_ID, SECOND_FILL_ID, RESUME_FILL_ID, SECOND_RESUME_FILL_ID}
    )
    assert kernel._execution_sessions[ENTRY_ORDER_ID].state.lifecycle.status == "FILLED"
    assert kernel._execution_sessions[SECOND_ORDER_ID].state.lifecycle.status == "FILLED"
    assert kernel._execution_sessions[ENTRY_ORDER_ID].state.portfolio == restored_ledger.state
    assert kernel._execution_sessions[SECOND_ORDER_ID].state.portfolio == restored_ledger.state
