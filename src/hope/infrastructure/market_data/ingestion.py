from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Mapping
from uuid import UUID

from pydantic import ValidationError

from hope.domain.market_data.models import MarketBar


class IngestionRejectionReason(StrEnum):
    INVALID_SOURCE_IDENTITY = "INVALID_SOURCE_IDENTITY"
    UNRESOLVED_INSTRUMENT = "UNRESOLVED_INSTRUMENT"
    DUPLICATE_SOURCE_BAR = "DUPLICATE_SOURCE_BAR"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    INVALID_NUMERIC = "INVALID_NUMERIC"
    INVALID_MARKET_BAR = "INVALID_MARKET_BAR"


@dataclass(frozen=True)
class RawMarketBar:
    """Provider payload after transport decoding but before canonicalization."""

    source: str
    source_symbol: str
    event_time: datetime
    available_time: datetime
    ingestion_time: datetime
    open: object
    high: object
    low: object
    close: object
    volume: object
    effective_time: datetime | None = None


@dataclass(frozen=True)
class IngestionRejection:
    index: int
    source: str
    source_symbol: str
    event_time: datetime
    reason: IngestionRejectionReason


@dataclass(frozen=True)
class NormalizedMarketBarBatch:
    bars: tuple[MarketBar, ...]
    rejections: tuple[IngestionRejection, ...]
    input_count: int

    @property
    def safe_to_persist(self) -> bool:
        return self.input_count > 0 and not self.rejections


def normalize_source_bars(
    raw_bars: tuple[RawMarketBar, ...] | list[RawMarketBar],
    *,
    identity_map: Mapping[tuple[str, str], UUID],
) -> NormalizedMarketBarBatch:
    """Normalize one provider batch without silently repairing unsafe evidence.

    Identity values must already be canonical and resolved. Every duplicate
    source key is quarantined, rather than choosing a winner based on arrival
    order. The returned bars are sorted deterministically.
    """

    items = tuple(raw_bars)
    keys = tuple((bar.source, bar.source_symbol, bar.event_time) for bar in items)
    counts = Counter(keys)
    accepted: list[MarketBar] = []
    rejected: list[IngestionRejection] = []

    for index, raw in enumerate(items):
        reason = _preflight_rejection(raw, counts=counts, identity_map=identity_map)
        if reason is not None:
            rejected.append(_rejection(index, raw, reason))
            continue

        try:
            numeric_values = {
                "open": _decimal(raw.open),
                "high": _decimal(raw.high),
                "low": _decimal(raw.low),
                "close": _decimal(raw.close),
                "volume": _decimal(raw.volume),
            }
        except (InvalidOperation, TypeError, ValueError):
            rejected.append(
                _rejection(index, raw, IngestionRejectionReason.INVALID_NUMERIC)
            )
            continue

        try:
            market_bar = MarketBar(
                instrument_id=str(identity_map[(raw.source, raw.source_symbol)]),
                event_time=raw.event_time,
                available_time=raw.available_time,
                effective_time=raw.effective_time,
                ingestion_time=raw.ingestion_time,
                **numeric_values,
            )
        except ValidationError:
            rejected.append(
                _rejection(index, raw, IngestionRejectionReason.INVALID_MARKET_BAR)
            )
            continue

        accepted.append(market_bar)

    accepted.sort(
        key=lambda bar: (
            bar.event_time,
            bar.available_time,
            bar.ingestion_time,
            bar.instrument_id,
        )
    )
    return NormalizedMarketBarBatch(
        bars=tuple(accepted),
        rejections=tuple(rejected),
        input_count=len(items),
    )


def _preflight_rejection(
    raw: RawMarketBar,
    *,
    counts: Counter[tuple[str, str, datetime]],
    identity_map: Mapping[tuple[str, str], UUID],
) -> IngestionRejectionReason | None:
    if (
        not raw.source
        or not raw.source_symbol
        or raw.source != raw.source.strip()
        or raw.source_symbol != raw.source_symbol.strip()
    ):
        return IngestionRejectionReason.INVALID_SOURCE_IDENTITY
    if counts[(raw.source, raw.source_symbol, raw.event_time)] > 1:
        return IngestionRejectionReason.DUPLICATE_SOURCE_BAR
    if (raw.source, raw.source_symbol) not in identity_map:
        return IngestionRejectionReason.UNRESOLVED_INSTRUMENT

    timestamps = (
        raw.event_time,
        raw.available_time,
        raw.effective_time,
        raw.ingestion_time,
    )
    if any(
        value is not None
        and (value.tzinfo is None or value.utcoffset() is None)
        for value in timestamps
    ):
        return IngestionRejectionReason.INVALID_TIMESTAMP
    return None


def _decimal(value: object) -> Decimal:
    converted = Decimal(str(value))
    if not converted.is_finite():
        raise ValueError("NON_FINITE_MARKET_DATA")
    return converted


def _rejection(
    index: int,
    raw: RawMarketBar,
    reason: IngestionRejectionReason,
) -> IngestionRejection:
    return IngestionRejection(
        index=index,
        source=raw.source,
        source_symbol=raw.source_symbol,
        event_time=raw.event_time,
        reason=reason,
    )
