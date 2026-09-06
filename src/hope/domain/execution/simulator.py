from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from hope.domain.execution.models import Order, OrderSide
from hope.domain.execution.timeline import ExecutionTimeline


@dataclass(frozen=True)
class ExecutionQuote:
    instrument_id: UUID
    event_time: datetime
    bid: Decimal
    ask: Decimal

    def __post_init__(self) -> None:
        if self.event_time.tzinfo is None or self.event_time.utcoffset() is None:
            raise ValueError("QUOTE_EVENT_TIME_MUST_BE_TIMEZONE_AWARE")
        if self.bid <= 0 or self.ask <= 0:
            raise ValueError("QUOTE_PRICES_MUST_BE_POSITIVE")
        if self.ask < self.bid:
            raise ValueError("QUOTE_ASK_BELOW_BID")


@dataclass(frozen=True)
class CostModel:
    version: str
    commission_rate: Decimal = Decimal("0")
    slippage_bps: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("COST_MODEL_VERSION_REQUIRED")
        if self.commission_rate < 0 or self.slippage_bps < 0:
            raise ValueError("COST_MODEL_VALUES_MUST_BE_NON_NEGATIVE")


@dataclass(frozen=True)
class Fill:
    fill_id: UUID
    order_id: UUID
    signal_id: UUID
    instrument_id: UUID
    side: OrderSide
    quantity: Decimal
    price: Decimal
    commission: Decimal
    slippage: Decimal
    cost_model_version: str
    fill_time: datetime | None = None


def simulate_market_fill(
    order: Order,
    quote: ExecutionQuote,
    fill_id: UUID,
    cost_model: CostModel,
    quantity: Decimal | None = None,
    *,
    timeline: ExecutionTimeline,
) -> Fill:
    if order.instrument_id != quote.instrument_id:
        raise ValueError("ORDER_QUOTE_INSTRUMENT_MISMATCH")
    timeline.assert_quote_eligible(quote.event_time)
    if fill_id is None:
        raise ValueError("FILL_ID_REQUIRED")
    fill_quantity = order.quantity if quantity is None else quantity
    if fill_quantity <= 0:
        raise ValueError("FILL_QUANTITY_MUST_BE_POSITIVE")
    if fill_quantity > order.quantity:
        raise ValueError("FILL_QUANTITY_EXCEEDS_ORDER_QUANTITY")
    if order.environment.value in {"PAPER", "RESEARCH", "BACKTEST", "WALK_FORWARD"}:
        base_price = quote.ask if order.side is OrderSide.BUY else quote.bid
        direction = Decimal("1") if order.side is OrderSide.BUY else Decimal("-1")
        slippage_per_unit = base_price * cost_model.slippage_bps / Decimal("10000")
        fill_price = base_price + direction * slippage_per_unit
        notional = fill_price * fill_quantity
        commission = notional * cost_model.commission_rate
        return Fill(fill_id, order.order_id, order.signal_id, order.instrument_id, order.side,
                    fill_quantity, fill_price, commission, slippage_per_unit * fill_quantity,
                    cost_model.version, quote.event_time)
    raise ValueError("UNSUPPORTED_EXECUTION_ENVIRONMENT")
