from datetime import datetime, timezone
from decimal import Decimal
import pytest
from hope.domain.market_data.models import MarketBar


def test_naive_market_data_timestamps_are_not_silently_accepted_as_orderable():
    t = datetime(2026, 8, 28, 10)
    with pytest.raises(ValueError):
        MarketBar(instrument_id="X", event_time=t, available_time=t, ingestion_time=t,
                  open=Decimal("1"), high=Decimal("1"), low=Decimal("1"), close=Decimal("1"), volume=Decimal("1"))


def test_negative_volume_is_rejected_at_domain_boundary():
    t = datetime(2026, 8, 28, 10, tzinfo=timezone.utc)
    with pytest.raises(Exception):
        MarketBar(instrument_id="X", event_time=t, available_time=t, ingestion_time=t,
                  open=Decimal("1"), high=Decimal("1"), low=Decimal("1"), close=Decimal("1"), volume=Decimal("-1"))
