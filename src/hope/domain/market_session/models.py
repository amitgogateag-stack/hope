from datetime import datetime
from enum import StrEnum
from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class Market(StrEnum):
    INDIA = "INDIA"
    USA = "USA"


class SessionState(StrEnum):
    CLOSED = "CLOSED"
    PRE_OPEN = "PRE_OPEN"
    OPEN = "OPEN"
    POST_CLOSE = "POST_CLOSE"


class MarketContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    market: Market
    as_of: datetime
    session_state: SessionState
    session_id: str
    is_trading_session: bool

    @field_validator("session_id")
    @classmethod
    def require_canonical_session_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("MARKET_SESSION_ID_REQUIRED")
        if value != value.strip():
            raise ValueError("MARKET_SESSION_ID_NOT_CANONICAL")
        return value

    @model_validator(mode="after")
    def validate_session(self):
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("market context timestamp must be timezone-aware")
        if self.session_state is SessionState.OPEN and not self.is_trading_session:
            raise ValueError("OPEN session must be a trading session")
        return self
