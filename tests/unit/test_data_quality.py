from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest
from hope.application.market_data.calendar import MarketSessionCalendar
from hope.application.market_data.quality import validate_bar, validate_bars
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


def test_batch_detects_duplicate_instrument_event_key_and_is_unsafe():
    duplicate = bar()
    report = validate_bars((duplicate, duplicate))
    assert report.states == (DataQualityState.VALID, DataQualityState.DUPLICATE)
    assert report.duplicate_keys == (("X", duplicate.event_time),)
    assert report.invalid_indices == (1,)
    assert report.safe is False


def test_batch_detects_missing_expected_instrument():
    report = validate_bars((bar(),), expected_instrument_ids=("X", "Y"))
    assert report.missing_instrument_ids == ("Y",)
    assert report.states == (DataQualityState.VALID, DataQualityState.MISSING)
    assert report.safe is False


def test_batch_is_safe_only_with_valid_unique_complete_evidence():
    first = bar(instrument_id="X")
    second = bar(instrument_id="Y")
    report = validate_bars((first, second), expected_instrument_ids=("X", "Y"))
    assert report.states == (DataQualityState.VALID, DataQualityState.VALID)
    assert report.safe is True


def test_empty_batch_is_unsafe_even_without_expected_universe():
    report = validate_bars(())
    assert report.empty_input is True
    assert report.states == ()
    assert report.safe is False


def test_expected_interval_detects_missing_middle_bar():
    first = bar(event_time=datetime(2026, 8, 28, 10, tzinfo=timezone.utc))
    third = bar(event_time=datetime(2026, 8, 28, 10, 2, tzinfo=timezone.utc), available_time=datetime(2026, 8, 28, 10, 2, tzinfo=timezone.utc), ingestion_time=datetime(2026, 8, 28, 10, 2, tzinfo=timezone.utc))
    report = validate_bars((first, third), expected_interval=timedelta(minutes=1))
    assert report.gap_keys == (("X", first.event_time, third.event_time),)
    assert report.safe is False


def test_non_positive_expected_interval_is_rejected():
    with pytest.raises(ValueError, match="EXPECTED_INTERVAL_MUST_BE_POSITIVE"):
        validate_bars((bar(),), expected_interval=timedelta(0))


def test_out_of_order_events_are_unsafe():
    later = bar(event_time=datetime(2026, 8, 28, 10, 1, tzinfo=timezone.utc), available_time=datetime(2026, 8, 28, 10, 1, tzinfo=timezone.utc), ingestion_time=datetime(2026, 8, 28, 10, 1, tzinfo=timezone.utc))
    earlier = bar()
    report = validate_bars((later, earlier))
    assert report.ordering_violations == (1,)
    assert report.safe is False


def test_session_calendar_does_not_flag_overnight_gap():
    first = bar(event_time=datetime(2026, 8, 28, 15, 59, tzinfo=timezone.utc), available_time=datetime(2026, 8, 28, 15, 59, tzinfo=timezone.utc), ingestion_time=datetime(2026, 8, 28, 15, 59, tzinfo=timezone.utc))
    next_session = bar(event_time=datetime(2026, 8, 29, 10, tzinfo=timezone.utc), available_time=datetime(2026, 8, 29, 10, tzinfo=timezone.utc), ingestion_time=datetime(2026, 8, 29, 10, tzinfo=timezone.utc))
    calendar = MarketSessionCalendar(sessions=((datetime(2026, 8, 28, 10, tzinfo=timezone.utc), datetime(2026, 8, 28, 16, tzinfo=timezone.utc)), (datetime(2026, 8, 29, 10, tzinfo=timezone.utc), datetime(2026, 8, 29, 16, tzinfo=timezone.utc))))
    report = validate_bars((first, next_session), expected_interval=timedelta(minutes=1), session_calendar=calendar)
    assert report.gap_keys == ()
    assert report.safe is True


def test_session_calendar_rejects_bar_outside_declared_session():
    calendar = MarketSessionCalendar(sessions=((datetime(2026, 8, 28, 10, tzinfo=timezone.utc), datetime(2026, 8, 28, 16, tzinfo=timezone.utc)),))
    report = validate_bars((bar(event_time=datetime(2026, 8, 28, 9, tzinfo=timezone.utc), available_time=datetime(2026, 8, 28, 9, tzinfo=timezone.utc), ingestion_time=datetime(2026, 8, 28, 9, tzinfo=timezone.utc)),), session_calendar=calendar)
    assert report.states == (DataQualityState.INCOMPLETE_SESSION,)
    assert report.safe is False
