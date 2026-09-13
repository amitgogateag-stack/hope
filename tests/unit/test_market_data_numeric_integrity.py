from datetime import datetime, timezone
from decimal import Decimal

import pytest

from hope.domain.market_data.models import MarketBar


NOW = datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_market_bar_rejects_non_finite_numerics(field: str, value: Decimal) -> None:
    values = {
        "open": Decimal("100"),
        "high": Decimal("101"),
        "low": Decimal("99"),
        "close": Decimal("100"),
        "volume": Decimal("1000"),
    }
    values[field] = value

    with pytest.raises(ValueError):
        MarketBar(
            instrument_id="X",
            event_time=NOW,
            available_time=NOW,
            ingestion_time=NOW,
            **values,
        )
