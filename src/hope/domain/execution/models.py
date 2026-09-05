from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field
from uuid import UUID
from decimal import Decimal


class Environment(StrEnum):
    RESEARCH = "RESEARCH"
    BACKTEST = "BACKTEST"
    WALK_FORWARD = "WALK_FORWARD"
    PAPER = "PAPER"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class Order(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    order_id: UUID
    signal_id: UUID
    instrument_id: UUID
    side: OrderSide
    quantity: Decimal = Field(gt=0)
    environment: Environment

    def assert_paper_safe(self) -> None:
        # There is intentionally no LIVE environment in v0.1.
        if self.environment not in Environment:
            raise ValueError("UNSUPPORTED_EXECUTION_ENVIRONMENT")
