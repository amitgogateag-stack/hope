from __future__ import annotations

from typing import Mapping, Protocol, runtime_checkable
from uuid import UUID

from hope.application.market_data.calendar import MarketSessionCalendar
from hope.infrastructure.market_data.ingestion import NormalizedMarketBarBatch
from hope.infrastructure.market_data.persistence import MarketBarSink
from hope.infrastructure.market_data.pipeline import ingest_market_data_window
from hope.infrastructure.market_data.provider import MarketDataProvider, MarketDataRequest


@runtime_checkable
class MarketDataCoverageVerifier(Protocol):
    def verify(
        self,
        dataset_version_id: UUID,
        requests: tuple[MarketDataRequest, ...],
        *,
        identity_map: Mapping[tuple[str, str], UUID],
    ) -> None:
        ...


@runtime_checkable
class MarketDataVersionSealer(Protocol):
    def seal(self, dataset_version_id: UUID) -> None:
        ...


def ingest_and_seal_market_data_windows(
    provider: MarketDataProvider,
    sink: MarketBarSink,
    coverage_verifier: MarketDataCoverageVerifier,
    sealer: MarketDataVersionSealer,
    dataset_version_id: UUID,
    requests: tuple[MarketDataRequest, ...],
    *,
    identity_map: Mapping[tuple[str, str], UUID],
    session_calendar: MarketSessionCalendar | None = None,
) -> tuple[NormalizedMarketBarBatch, ...]:
    """Ingest a deterministic request set, verify persisted coverage, then seal.

    Without a session calendar, windows must remain exactly contiguous. With a
    trusted calendar, closed-market gaps are allowed only when no expected slot
    exists between windows, and every requested event slot must be an in-session
    interval-grid timestamp. This preserves fail-closed completeness while
    allowing real overnight/weekend/holiday boundaries.
    """

    if not isinstance(dataset_version_id, UUID):
        raise TypeError("MARKET_DATA_LIFECYCLE_REQUIRES_DATASET_VERSION_ID")
    if not requests:
        raise ValueError("MARKET_DATA_LIFECYCLE_WINDOWS_REQUIRED")

    first = requests[0]
    for request in requests:
        if (
            request.source != first.source
            or request.source_symbols != first.source_symbols
            or request.interval != first.interval
        ):
            raise ValueError("MARKET_DATA_LIFECYCLE_WINDOWS_NOT_HOMOGENEOUS")
        if session_calendar is not None:
            requested_times = tuple(
                event_time for _, event_time in request.expected_keys[::len(request.source_symbols)]
            )
            expected_times = session_calendar.expected_times(
                request.start,
                request.end,
                request.interval,
            )
            if requested_times != expected_times:
                raise ValueError("MARKET_DATA_LIFECYCLE_WINDOW_OUTSIDE_SESSION")

    for previous, current in zip(requests, requests[1:]):
        if current.start == previous.end:
            continue
        if session_calendar is None:
            raise ValueError("MARKET_DATA_LIFECYCLE_WINDOWS_NOT_CONTIGUOUS")
        if session_calendar.expected_times(
            previous.end,
            current.start,
            previous.interval,
        ):
            raise ValueError("MARKET_DATA_LIFECYCLE_WINDOWS_SKIP_TRADING_SLOTS")

    required_identity_keys = {
        (request.source, symbol)
        for request in requests
        for symbol in request.source_symbols
    }
    if any(key not in identity_map for key in required_identity_keys):
        raise ValueError("MARKET_DATA_LIFECYCLE_IDENTITY_MAP_INCOMPLETE")
    if any(not isinstance(identity_map[key], UUID) for key in required_identity_keys):
        raise TypeError("MARKET_DATA_LIFECYCLE_IDENTITY_MAP_INVALID")

    batches = tuple(
        ingest_market_data_window(
            provider,
            sink,
            dataset_version_id,
            request,
            identity_map=identity_map,
        )
        for request in requests
    )
    coverage_verifier.verify(
        dataset_version_id,
        requests,
        identity_map=identity_map,
    )
    sealer.seal(dataset_version_id)
    return batches
