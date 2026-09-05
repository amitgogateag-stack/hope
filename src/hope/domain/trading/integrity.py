from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class TradeLinkContext:
    signal_id: UUID
    signal_instrument_id: UUID
    order_instrument_id: UUID
    position_instrument_id: UUID | None = None


def validate_trade_links(context: TradeLinkContext) -> None:
    if context.signal_instrument_id != context.order_instrument_id:
        raise ValueError("ORDER_SIGNAL_INSTRUMENT_MISMATCH")
    if context.position_instrument_id is not None and context.signal_instrument_id != context.position_instrument_id:
        raise ValueError("POSITION_SIGNAL_INSTRUMENT_MISMATCH")
