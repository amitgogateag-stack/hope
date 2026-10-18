"""One immutable binding snapshot across provider and manifest callbacks."""
from types import MappingProxyType
from typing import Mapping, TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from hope.infrastructure.market_data.provider import MarketDataRequest


def snapshot_request_bindings(
    requests: tuple["MarketDataRequest", ...],
    identity_map: Mapping[tuple[str, str], UUID],
) -> Mapping[tuple[str, str], UUID]:
    """Copy only requested keys once; preserve each caller's validation policy.

    Missing/invalid values remain missing/invalid so public error/rejection
    contracts stay unchanged. Manifest preflight must validate this same frozen
    snapshot before any provider effect. No caller-owned mapping is retained.
    """
    keys = sorted({(request.source,symbol) for request in requests
                   for symbol in request.source_symbols})
    missing = object()
    captured = {}
    for key in keys:
        value = identity_map.get(key, missing)
        if value is not missing:
            captured[key] = value
    return MappingProxyType(captured)
