from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from hope.infrastructure.repositories.market_intelligence import (
    SqlAlchemyMarketIntelligenceRepository,
)


class Rows:
    def __init__(self, row: dict | None) -> None:
        self._row = row

    def mappings(self):
        return self

    def one_or_none(self):
        return self._row


class Connection:
    def __init__(self, row: dict | None) -> None:
        self._row = row

    def execute(self, statement):
        return Rows(self._row)


def event_row() -> dict:
    event_time = datetime(2026, 1, 1, 12, tzinfo=UTC)
    return {
        "event_id": uuid4(),
        "scope": "COMPANY",
        "instrument_id": uuid4(),
        "universe_version_id": None,
        "source": "exchange-feed",
        "source_item_id": "notice-1",
        "source_tier": 1,
        "category": "REGULATORY",
        "materiality": "HIGH",
        "recommended_action": "BLOCK_NEW_ENTRY",
        "event_time": event_time,
        "available_time": event_time + timedelta(minutes=1),
        "ingestion_time": event_time + timedelta(minutes=2),
        "source_payload_hash": "a" * 64,
        "created_at": event_time + timedelta(minutes=3),
    }


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        (
            {"source": " exchange-feed "},
            "INTELLIGENCE_SOURCE_IDENTITY_NOT_CANONICAL",
        ),
        (
            {"available_time": datetime(2026, 1, 1, 11, 59, tzinfo=UTC)},
            "INTELLIGENCE_AVAILABLE_TIME_PRECEDES_EVENT",
        ),
        (
            {"source_tier": 4},
            "INTELLIGENCE_LOW_AUTHORITY_ACTION_NOT_ALLOWED",
        ),
    ],
)
def test_market_intelligence_repository_revalidates_stored_event(
    changes: dict,
    error: str,
) -> None:
    row = {**event_row(), **changes}
    repository = SqlAlchemyMarketIntelligenceRepository(Connection(row))

    with pytest.raises(ValueError, match=error):
        repository.get(row["event_id"])
