from dataclasses import dataclass
from datetime import datetime
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


@dataclass(frozen=True)
class ExecutionRejection:
    """Terminal execution outcome for an order that has not received any fill."""

    order_id: UUID
    signal_id: UUID
    instrument_id: UUID
    environment: Environment
    reason_code: str
    rejection_time: datetime

    def __post_init__(self) -> None:
        if not self.reason_code.strip():
            raise ValueError("EXECUTION_REJECTION_REASON_REQUIRED")
        if self.rejection_time.tzinfo is None or self.rejection_time.utcoffset() is None:
            raise ValueError("EXECUTION_REJECTION_TIME_MUST_BE_TIMEZONE_AWARE")


@dataclass(frozen=True)
class ExecutionCancellation:
    """Terminal cancellation outcome for an open or partially filled order."""

    order_id: UUID
    signal_id: UUID
    instrument_id: UUID
    environment: Environment
    reason_code: str
    cancellation_time: datetime
    cancelled_quantity: Decimal

    def __post_init__(self) -> None:
        if not self.reason_code.strip():
            raise ValueError("EXECUTION_CANCELLATION_REASON_REQUIRED")
        if self.cancellation_time.tzinfo is None or self.cancellation_time.utcoffset() is None:
            raise ValueError("EXECUTION_CANCELLATION_TIME_MUST_BE_TIMEZONE_AWARE")
        if self.cancelled_quantity <= 0:
            raise ValueError("EXECUTION_CANCELLATION_QUANTITY_MUST_BE_POSITIVE")
