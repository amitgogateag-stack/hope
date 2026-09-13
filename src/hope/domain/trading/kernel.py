from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from hope.domain.execution.models import Environment, Order, OrderSide
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.domain.signal.models import Signal, SignalType


@dataclass(frozen=True)
class OrderIntent:
    signal_id: UUID
    instrument_id: UUID
    side: OrderSide
    quantity: Decimal
    environment: Environment
    signal_type: SignalType


def create_order_intent(signal: Signal, risk: RiskAssessment, side: OrderSide, environment: Environment) -> OrderIntent | None:
    if risk.signal_id != signal.signal_id:
        raise ValueError("RISK_SIGNAL_MISMATCH")
    if risk.decision is RiskDecision.REJECT:
        return None
    if risk.approved_quantity <= 0:
        raise ValueError("APPROVED_QUANTITY_MUST_BE_POSITIVE")
    if environment not in Environment:
        raise ValueError("UNSUPPORTED_EXECUTION_ENVIRONMENT")
    return OrderIntent(
        signal.signal_id,
        signal.instrument_id,
        side,
        risk.approved_quantity,
        environment,
        signal.signal_type,
    )


def materialize_order(intent: OrderIntent, order_id: UUID) -> Order:
    """Materialize an order for the intent's declared execution environment.

    This is a shared research/backtest/PAPER domain primitive. PAPER-only safety is
    enforced at the PAPER application/persistence boundary, not here, so BACKTEST
    orders remain valid deterministic simulation evidence.
    """
    return Order(
        order_id=order_id,
        signal_id=intent.signal_id,
        instrument_id=intent.instrument_id,
        side=intent.side,
        quantity=intent.quantity,
        environment=intent.environment,
        signal_type=intent.signal_type,
    )
