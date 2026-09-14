from enum import StrEnum
from uuid import UUID
from pydantic import BaseModel, ConfigDict, field_validator, model_validator


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
    canonical_symbol: str
    exchange: str
    status: IdentityStatus = IdentityStatus.ACTIVE

    @field_validator("canonical_symbol", "exchange")
    @classmethod
    def require_canonical_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("INSTRUMENT_IDENTITY_REQUIRED")
        if value != value.strip():
            raise ValueError("INSTRUMENT_IDENTITY_NOT_CANONICAL")
        return value


class IdentityMapping(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    source_symbol: str
    broker_instrument_id: str
    canonical_instrument_id: UUID | None = None
    status: IdentityStatus
    reason: str | None = None

    @field_validator("source_symbol", "broker_instrument_id")
    @classmethod
    def require_canonical_mapping_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("IDENTITY_MAPPING_VALUE_REQUIRED")
        if value != value.strip():
            raise ValueError("IDENTITY_MAPPING_VALUE_NOT_CANONICAL")
        return value

    @model_validator(mode="after")
    def validate_mapping_state(self):
        if self.status is IdentityStatus.ACTIVE and self.canonical_instrument_id is None:
            raise ValueError("ACTIVE identity mapping requires canonical_instrument_id")
        if self.status is IdentityStatus.TERMINAL and self.canonical_instrument_id is not None:
            raise ValueError("TERMINAL identity mapping cannot carry canonical_instrument_id")
        return self
