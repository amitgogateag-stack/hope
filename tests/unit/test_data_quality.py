from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest
from hope.application.market_data.quality import validate_bar
from hope.domain.market_data.models import MarketBar, DataQualityState


def bar(**kwargs):
    t = datetime(2026, 8, 28, 10, tzinfo=timezone.utc)
    values = dict(instrument_id="X", event_time=t, available_time=t, ingestion_time=t,
                  open=Decimal("10"), high=Decimal("11"), low=Decimal("9"), close=Decimal("10.5"), volume=Decimal("100"))
    values.update(kwargs)
    return MarketBar(**values)


def test_valid_bar_is_valid():
    assert validate_bar(bar()) is DataQualityState.VALID


def test_lagging_bar_is_stale():
    expected = datetime(2026, 8, 28, 10, tzinfo=timezone.utc)
    assert validate_bar(bar(event_time=expected - timedelta(minutes=1), available_time=expected - timedelta(minutes=1), ingestion_time=expected), expected_latest_event_time=expected) is DataQualityState.STALE


def test_future_bar_is_future():
    expected = datetime(2026, 8, 28, 10, tzinfo=timezone.utc)
    future = expected + timedelta(minutes=1)
    assert validate_bar(bar(event_time=future, available_time=future, ingestion_time=future), expected_latest_event_time=expected) is DataQualityState.FUTURE


def test_bad_ohlc_is_rejected_at_domain_boundary():
    with pytest.raises(Exception):
        bar(high=Decimal("8"))


def test_naive_expected_timestamp_is_rejected():
    with pytest.raises(ValueError):
        validate_bar(bar(), expected_latest_event_time=datetime(2026, 8, 28, 10))
