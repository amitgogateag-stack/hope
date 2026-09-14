from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from hope.infrastructure.market_data.ingestion import (
    IngestionRejectionReason,
    RawMarketBar,
    normalize_source_bars,
)


NOW = datetime(2026, 9, 14, 14, 0, tzinfo=timezone.utc)


def raw_bar(
    *,
    source: str = "TEST",
    symbol: str = "ABC",
    event_time: datetime = NOW,
    available_time: datetime | None = None,
    ingestion_time: datetime | None = None,
    open_value: object = "100",
    high_value: object = "101",
    low_value: object = "99",
    close_value: object = "100.5",
    volume: object = "1000",
) -> RawMarketBar:
    return RawMarketBar(
        source=source,
        source_symbol=symbol,
        event_time=event_time,
        available_time=available_time or event_time,
        ingestion_time=ingestion_time or available_time or event_time,
        open=open_value,
        high=high_value,
        low=low_value,
        close=close_value,
        volume=volume,
    )


def test_normalizes_resolved_bar_and_preserves_decimal_values() -> None:
    instrument_id = uuid4()

    result = normalize_source_bars(
        [raw_bar()],
        identity_map={("TEST", "ABC"): instrument_id},
    )

    assert result.safe_to_persist
    assert result.input_count == 1
    assert result.rejections == ()
    assert result.bars[0].instrument_id == str(instrument_id)
    assert result.bars[0].close == Decimal("100.5")


def test_unresolved_identity_is_quarantined() -> None:
    result = normalize_source_bars([raw_bar()], identity_map={})

    assert not result.safe_to_persist
    assert result.bars == ()
    assert result.rejections[0].reason is IngestionRejectionReason.UNRESOLVED_INSTRUMENT


def test_duplicate_source_keys_quarantine_every_copy() -> None:
    instrument_id = uuid4()
    item = raw_bar()

    result = normalize_source_bars(
        [item, item],
        identity_map={("TEST", "ABC"): instrument_id},
    )

    assert result.bars == ()
    assert [rejection.reason for rejection in result.rejections] == [
        IngestionRejectionReason.DUPLICATE_SOURCE_BAR,
        IngestionRejectionReason.DUPLICATE_SOURCE_BAR,
    ]


def test_noncanonical_source_identity_is_rejected() -> None:
    result = normalize_source_bars(
        [raw_bar(source=" TEST")],
        identity_map={(" TEST", "ABC"): uuid4()},
    )

    assert result.rejections[0].reason is IngestionRejectionReason.INVALID_SOURCE_IDENTITY


def test_naive_timestamp_is_rejected_before_domain_construction() -> None:
    naive = NOW.replace(tzinfo=None)
    result = normalize_source_bars(
        [raw_bar(event_time=naive, available_time=naive, ingestion_time=naive)],
        identity_map={("TEST", "ABC"): uuid4()},
    )

    assert result.rejections[0].reason is IngestionRejectionReason.INVALID_TIMESTAMP


def test_invalid_ohlc_is_quarantined() -> None:
    result = normalize_source_bars(
        [raw_bar(high_value="98")],
        identity_map={("TEST", "ABC"): uuid4()},
    )

    assert result.bars == ()
    assert result.rejections[0].reason is IngestionRejectionReason.INVALID_MARKET_BAR


def test_output_order_is_deterministic_not_provider_arrival_order() -> None:
    first_id = uuid4()
    second_id = uuid4()
    later = raw_bar(symbol="LATER", event_time=NOW + timedelta(minutes=1))
    earlier = raw_bar(symbol="EARLIER")

    result = normalize_source_bars(
        [later, earlier],
        identity_map={
            ("TEST", "LATER"): first_id,
            ("TEST", "EARLIER"): second_id,
        },
    )

    assert result.safe_to_persist
    assert [bar.event_time for bar in result.bars] == [
        NOW,
        NOW + timedelta(minutes=1),
    ]


def test_empty_batch_is_never_safe_to_persist() -> None:
    result = normalize_source_bars([], identity_map={})

    assert not result.safe_to_persist
    assert result.input_count == 0
