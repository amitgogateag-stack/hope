from datetime import datetime, timezone
import pytest
from hope.domain.market_session import Market, MarketContext, SessionState


def test_open_session_must_be_trading_session():
    with pytest.raises(ValueError, match="OPEN session"):
        MarketContext(market=Market.USA, as_of=datetime.now(timezone.utc), session_state=SessionState.OPEN, session_id="x", is_trading_session=False)


def test_market_context_requires_timezone():
    with pytest.raises(ValueError, match="timezone-aware"):
        MarketContext(market=Market.INDIA, as_of=datetime(2026, 8, 29), session_state=SessionState.CLOSED, session_id="x", is_trading_session=False)
