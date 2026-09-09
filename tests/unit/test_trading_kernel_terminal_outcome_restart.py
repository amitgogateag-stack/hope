from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from hope.application.trading.service import TradingKernel
from hope.domain.execution.models import (
    Environment,
    ExecutionCancellation,
    ExecutionRejection,
    OrderSide,
)
from hope.domain.execution.replay import replay_order
from hope.domain.execution.simulator import Fill
from hope.domain.portfolio.ledger import PortfolioLedger
from hope.domain.signal.models import SignalType
from hope.domain.trading.kernel import OrderIntent, materialize_order


UTC = timezone.utc
INSTRUMENT_ID = UUID("12121212-1212-1212-1212-121212121212")
CANCEL_SIGNAL_ID = UUID("23232323-2323-2323-2323-232323232323")
CANCEL_ORDER_ID = UUID("34343434-3434-3434-3434-343434343434")
CANCEL_FILL_ID = UUID("45454545-4545-4545-4545-454545454545")
REJECT_SIGNAL_ID = UUID("56565656-5656-5656-5656-565656565656")
REJECT_ORDER_ID = UUID("67676767-6767-6767-6767-676767676767")


def _ledger_from_state(state):
    return PortfolioLedger.from_state(
        state.portfolio,
        applied_fill_ids=state.fill_ids,
    )


def test_cancelled_terminal_replay_restores_identity_without_resumable_session():
    order_time = datetime(2026, 1, 7, 14, 30, tzinfo=UTC)
    intent = OrderIntent(
        signal_id=CANCEL_SIGNAL_ID,
        instrument_id=INSTRUMENT_ID,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        environment=Environment.BACKTEST,
        signal_type=SignalType.ENTRY,
    )
    order = materialize_order(intent, CANCEL_ORDER_ID)
    fill = Fill(
        fill_id=CANCEL_FILL_ID,
        order_id=CANCEL_ORDER_ID,
        signal_id=CANCEL_SIGNAL_ID,
        instrument_id=INSTRUMENT_ID,
        side=OrderSide.BUY,
        quantity=Decimal("4"),
        price=Decimal("100"),
        commission=Decimal("0"),
        slippage=Decimal("0"),
        cost_model_version="cancel-restart-seed",
        fill_time=order_time,
    )
    cancellation = ExecutionCancellation(
        order_id=CANCEL_ORDER_ID,
        signal_id=CANCEL_SIGNAL_ID,
        instrument_id=INSTRUMENT_ID,
        environment=Environment.BACKTEST,
        reason_code="BROKER_CANCELLED",
        cancellation_time=order_time,
        cancelled_quantity=Decimal("6"),
    )

    replayed = replay_order(
        order,
        [fill],
        cancellation=cancellation,
        order_time=order_time,
        initial_cash=Decimal("10000"),
    )

    assert replayed.state.lifecycle.status == "CANCELLED"
    assert replayed.session is None
    assert replayed.cancellation_applied is True

    kernel = TradingKernel(_ledger_from_state(replayed.state))
    restored = kernel.restore_terminal_order(replayed.state)

    assert restored == replayed.state
    assert CANCEL_ORDER_ID not in kernel._execution_sessions
    assert kernel._terminal_orders[CANCEL_ORDER_ID] == order


def test_rejected_terminal_replay_restores_identity_without_resumable_session():
    order_time = datetime(2026, 1, 7, 14, 30, tzinfo=UTC)
    intent = OrderIntent(
        signal_id=REJECT_SIGNAL_ID,
        instrument_id=INSTRUMENT_ID,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        environment=Environment.BACKTEST,
        signal_type=SignalType.ENTRY,
    )
    order = materialize_order(intent, REJECT_ORDER_ID)
    rejection = ExecutionRejection(
        order_id=REJECT_ORDER_ID,
        signal_id=REJECT_SIGNAL_ID,
        instrument_id=INSTRUMENT_ID,
        environment=Environment.BACKTEST,
        reason_code="BROKER_REJECTED",
        rejection_time=order_time,
    )

    replayed = replay_order(
        order,
        [],
        rejection=rejection,
        order_time=order_time,
        initial_cash=Decimal("10000"),
    )

    assert replayed.state.lifecycle.status == "REJECTED"
    assert replayed.session is None
    assert replayed.rejection_applied is True

    kernel = TradingKernel(_ledger_from_state(replayed.state))
    restored = kernel.restore_terminal_order(replayed.state)

    assert restored == replayed.state
    assert REJECT_ORDER_ID not in kernel._execution_sessions
    assert kernel._terminal_orders[REJECT_ORDER_ID] == order
