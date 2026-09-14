from datetime import datetime, timezone, tzinfo
import pytest
from hope.domain.market_session import Market, MarketContext, SessionState


class MissingOffsetTimezone(tzinfo):
    def utcoffset(self, dt):
        return None


def test_open_session_must_be_trading_session():
    with pytest.raises(ValueError, match="OPEN session"):
        MarketContext(market=Market.USA, as_of=datetime.now(timezone.utc), session_state=SessionState.OPEN, session_id="x", is_trading_session=False)


def test_market_context_requires_timezone():
    with pytest.raises(ValueError, match="timezone-aware"):
        MarketContext(market=Market.INDIA, as_of=datetime(2026, 8, 29), session_state=SessionState.CLOSED, session_id="x", is_trading_session=False)


def test_market_context_rejects_timezone_without_utc_offset():
    with pytest.raises(ValueError, match="timezone-aware"):
        MarketContext(
            market=Market.USA,
            as_of=datetime(2026, 8, 29, tzinfo=MissingOffsetTimezone()),
            session_state=SessionState.CLOSED,
            session_id="x",
            is_trading_session=False,
        )


@pytest.mark.parametrize("session_id", ["", "   "])
def test_market_context_requires_nonblank_session_id(session_id):
    with pytest.raises(ValueError, match="MARKET_SESSION_ID_REQUIRED"):
        MarketContext(
            market=Market.USA,
            as_of=datetime(2026, 8, 29, tzinfo=timezone.utc),
            session_state=SessionState.CLOSED,
            session_id=session_id,
            is_trading_session=False,
        )


@pytest.mark.parametrize("session_id", [" 2026-08-29", "2026-08-29 "])
def test_market_context_requires_canonical_session_id(session_id):
    with pytest.raises(ValueError, match="MARKET_SESSION_ID_NOT_CANONICAL"):
        MarketContext(
            market=Market.INDIA,
            as_of=datetime(2026, 8, 29, tzinfo=timezone.utc),
            session_state=SessionState.CLOSED,
            session_id=session_id,
            is_trading_session=False,
        )
