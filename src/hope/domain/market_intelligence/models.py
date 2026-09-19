from __future__ import annotations

from datetime import datetime
from enum import IntEnum, StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class IntelligenceScope(StrEnum):
    COMPANY = "COMPANY"
    MARKET = "MARKET"


class IntelligenceCategory(StrEnum):
    EARNINGS = "EARNINGS"
    GUIDANCE = "GUIDANCE"
    CORPORATE_ACTION = "CORPORATE_ACTION"
    CAPITAL_RAISE = "CAPITAL_RAISE"
    MERGER_ACQUISITION = "MERGER_ACQUISITION"
    MANAGEMENT_CHANGE = "MANAGEMENT_CHANGE"
    REGULATORY = "REGULATORY"
    CREDIT = "CREDIT"
    CONTRACT = "CONTRACT"
    LITIGATION = "LITIGATION"
    INSOLVENCY = "INSOLVENCY"
    OWNERSHIP = "OWNERSHIP"
    TRADING_STATUS = "TRADING_STATUS"
    MACRO = "MACRO"
    GEOPOLITICAL = "GEOPOLITICAL"
    OTHER = "OTHER"


class IntelligenceMateriality(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class IntelligenceAction(StrEnum):
    NO_ACTION = "NO_ACTION"
    OBSERVE = "OBSERVE"
    BLOCK_NEW_ENTRY = "BLOCK_NEW_ENTRY"
    REDUCE_RISK_CANDIDATE = "REDUCE_RISK_CANDIDATE"
    EXIT_CANDIDATE = "EXIT_CANDIDATE"
    DATA_REVIEW_REQUIRED = "DATA_REVIEW_REQUIRED"
    MARKET_RISK_HALT_CANDIDATE = "MARKET_RISK_HALT_CANDIDATE"


class IntelligenceSourceTier(IntEnum):
    PRIMARY_REGULATORY_OR_EXCHANGE = 1
    OFFICIAL_COMPANY = 2
    ESTABLISHED_NEWS = 3
    SECONDARY_OR_SOCIAL = 4


class MarketIntelligenceEvent(BaseModel):
    """Provider-neutral immutable evidence event; never an order instruction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID
    scope: IntelligenceScope
    instrument_id: UUID | None = None
    universe_version_id: UUID | None = None
    source: str
    source_item_id: str
    source_tier: IntelligenceSourceTier
    category: IntelligenceCategory
    materiality: IntelligenceMateriality
    recommended_action: IntelligenceAction
    event_time: datetime
    available_time: datetime
    ingestion_time: datetime
    source_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("source", "source_item_id")
    @classmethod
    def require_canonical_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("INTELLIGENCE_SOURCE_IDENTITY_REQUIRED")
        if value != value.strip():
            raise ValueError("INTELLIGENCE_SOURCE_IDENTITY_NOT_CANONICAL")
        return value

    @model_validator(mode="after")
    def validate_scope_time_and_authority(self) -> "MarketIntelligenceEvent":
        for value in (self.event_time, self.available_time, self.ingestion_time):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("INTELLIGENCE_TIMESTAMP_MUST_BE_TIMEZONE_AWARE")
        if self.available_time < self.event_time:
            raise ValueError("INTELLIGENCE_AVAILABLE_TIME_PRECEDES_EVENT")
        if self.ingestion_time < self.available_time:
            raise ValueError("INTELLIGENCE_INGESTION_TIME_PRECEDES_AVAILABLE")
        if self.scope is IntelligenceScope.COMPANY and self.instrument_id is None:
            raise ValueError("INTELLIGENCE_COMPANY_INSTRUMENT_REQUIRED")
        if self.scope is IntelligenceScope.MARKET and self.instrument_id is not None:
            raise ValueError("INTELLIGENCE_MARKET_EVENT_MUST_NOT_BIND_INSTRUMENT")
        if (
            self.source_tier is IntelligenceSourceTier.SECONDARY_OR_SOCIAL
            and self.recommended_action
            not in {IntelligenceAction.NO_ACTION, IntelligenceAction.OBSERVE, IntelligenceAction.DATA_REVIEW_REQUIRED}
        ):
            raise ValueError("INTELLIGENCE_LOW_AUTHORITY_ACTION_NOT_ALLOWED")
        return self
