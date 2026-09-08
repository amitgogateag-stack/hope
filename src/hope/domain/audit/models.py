from datetime import datetime
from enum import StrEnum
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class AuditEventType(StrEnum):
    SIGNAL_ACCEPTED = "SIGNAL_ACCEPTED"
    RISK_APPROVED = "RISK_APPROVED"
    RISK_REJECTED = "RISK_REJECTED"
    ORDER_CREATED = "ORDER_CREATED"
    EXECUTION_REJECTED = "EXECUTION_REJECTED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    FILL_CREATED = "FILL_CREATED"
    PORTFOLIO_UPDATED = "PORTFOLIO_UPDATED"


class AuditEvent(BaseModel):
    """Immutable, attributable event emitted by the trading kernel."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    event_id: UUID
    event_type: AuditEventType
    event_time: datetime
    signal_id: UUID | None = None
    order_id: UUID | None = None
    fill_id: UUID | None = None
    instrument_id: UUID | None = None
    environment: str = Field(min_length=1)
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
