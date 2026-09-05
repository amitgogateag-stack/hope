from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class SignalType(StrEnum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"


class Signal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    signal_id: UUID
    instrument_id: UUID
    strategy_version: str = Field(min_length=1)
    decision_time: datetime
    signal_type: SignalType
    conviction: Decimal = Field(ge=0, le=1)
    inputs_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    def assert_point_in_time(self, available_time: datetime) -> None:
        if self.decision_time.tzinfo is None or available_time.tzinfo is None:
            raise ValueError("signal and available timestamps must be timezone-aware")
        if self.decision_time < available_time:
            raise ValueError("SIGNAL_USES_UNAVAILABLE_INFORMATION")
