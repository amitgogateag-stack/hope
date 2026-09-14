from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from hope.infrastructure.market_data.provider import MarketDataRequest
from hope.infrastructure.repositories.market_data_manifest import build_market_data_manifest


def test_manifest_rejects_fractional_second_cadence_without_truncation() -> None:
    start = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)
    request = MarketDataRequest(
        source="TEST",
        source_symbols=("ABC",),
        start=start,
        end=start + timedelta(seconds=1),
        interval=timedelta(milliseconds=500),
    )

    with pytest.raises(ValueError, match="INTERVAL_REQUIRES_WHOLE_SECONDS"):
        build_market_data_manifest(
            uuid4(),
            (request,),
            identity_map={("TEST", "ABC"): uuid4()},
        )
