from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest
from hope.domain.market_data.models import MarketBar

def test_available_time_cannot_precede_event_time():
    t = datetime(2026, 8, 28, 10, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        MarketBar(instrument_id="X", event_time=t, available_time=t-timedelta(minutes=1), ingestion_time=t, open=Decimal("1"), high=Decimal("1"), low=Decimal("1"), close=Decimal("1"), volume=Decimal("1"))

def test_ingestion_time_cannot_precede_available_time():
    t = datetime(2026, 8, 28, 10, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        MarketBar(instrument_id="X", event_time=t, available_time=t, ingestion_time=t-timedelta(minutes=1), open=Decimal("1"), high=Decimal("1"), low=Decimal("1"), close=Decimal("1"), volume=Decimal("1"))


@pytest.mark.parametrize("instrument_id", [" X", "X "])
def test_market_bar_instrument_id_must_be_canonical(instrument_id):
    t = datetime(2026, 8, 28, 10, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="MARKET_BAR_INSTRUMENT_ID_NOT_CANONICAL"):
        MarketBar(
            instrument_id=instrument_id,
            event_time=t,
            available_time=t,
            ingestion_time=t,
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
            volume=Decimal("1"),
        )
