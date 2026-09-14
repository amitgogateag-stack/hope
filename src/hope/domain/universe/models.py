from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class UniverseVersion(BaseModel):
    model_config = ConfigDict(frozen=True)

    universe_id: UUID
    version: str
    declared_member_count: int = Field(ge=0)
    pit_certified: bool = False

    @field_validator("version")
    @classmethod
    def require_canonical_version(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("UNIVERSE_VERSION_REQUIRED")
        if value != value.strip():
            raise ValueError("UNIVERSE_VERSION_NOT_CANONICAL")
        return value


class UniverseMember(BaseModel):
    model_config = ConfigDict(frozen=True)

    instrument_id: UUID
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    @model_validator(mode="after")
    def validate_interval(self) -> "UniverseMember":
        if self.valid_from and self.valid_to and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be after valid_from")
        return self
