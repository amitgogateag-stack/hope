from datetime import datetime
from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    session_id: str = Field(min_length=1)
    is_trading_session: bool

    @model_validator(mode="after")
    def validate_session(self):
        if self.as_of.tzinfo is None:
            raise ValueError("market context timestamp must be timezone-aware")
        if self.session_state is SessionState.OPEN and not self.is_trading_session:
            raise ValueError("OPEN session must be a trading session")
        return self
