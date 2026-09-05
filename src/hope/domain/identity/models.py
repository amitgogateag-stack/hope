from enum import StrEnum
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator


class IdentityStatus(StrEnum):
    ACTIVE = "ACTIVE"
    TERMINAL = "TERMINAL"
    DUPLICATE = "DUPLICATE"
    AMBIGUOUS = "AMBIGUOUS"
    UNRESOLVED = "UNRESOLVED"
    SUPERSEDED = "SUPERSEDED"


class Instrument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    instrument_id: UUID
    canonical_symbol: str = Field(min_length=1)
    exchange: str = Field(min_length=1)
    status: IdentityStatus = IdentityStatus.ACTIVE


class IdentityMapping(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    source_symbol: str = Field(min_length=1)
    broker_instrument_id: str = Field(min_length=1)
    canonical_instrument_id: UUID | None = None
    status: IdentityStatus
    reason: str | None = None

    @model_validator(mode="after")
    def validate_mapping_state(self):
        if self.status is IdentityStatus.ACTIVE and self.canonical_instrument_id is None:
            raise ValueError("ACTIVE identity mapping requires canonical_instrument_id")
        if self.status is IdentityStatus.TERMINAL and self.canonical_instrument_id is not None:
            raise ValueError("TERMINAL identity mapping cannot carry canonical_instrument_id")
        return self
