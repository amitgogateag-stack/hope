from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from hope.domain.market_intelligence.models import (
    IntelligenceAction,
    IntelligenceCategory,
    IntelligenceMateriality,
    IntelligenceScope,
    IntelligenceSourceTier,
    MarketIntelligenceEvent,
)
from hope.infrastructure.market_intelligence.provider import (
    IntelligenceRequest,
    MarketIntelligenceProvider,
    ProviderIntelligenceBatch,
    fetch_intelligence_events,
)


def _event(**overrides):
    base = dict(
        event_id=uuid4(),
        scope=IntelligenceScope.COMPANY,
        instrument_id=uuid4(),
        source="FIXTURE",
        source_item_id="item-1",
        source_tier=IntelligenceSourceTier.PRIMARY_REGULATORY_OR_EXCHANGE,
        category=IntelligenceCategory.EARNINGS,
        materiality=IntelligenceMateriality.HIGH,
        recommended_action=IntelligenceAction.BLOCK_NEW_ENTRY,
        event_time=datetime(2026, 9, 18, 12, tzinfo=timezone.utc),
        available_time=datetime(2026, 9, 18, 12, 1, tzinfo=timezone.utc),
        ingestion_time=datetime(2026, 9, 18, 12, 2, tzinfo=timezone.utc),
        source_payload_hash="a" * 64,
    )
    base.update(overrides)
    return MarketIntelligenceEvent(**base)


def test_low_authority_event_cannot_request_material_action() -> None:
    with pytest.raises(ValueError, match="INTELLIGENCE_LOW_AUTHORITY_ACTION_NOT_ALLOWED"):
        _event(
            source_tier=IntelligenceSourceTier.SECONDARY_OR_SOCIAL,
            recommended_action=IntelligenceAction.EXIT_CANDIDATE,
        )


def test_market_event_must_not_bind_single_instrument() -> None:
    with pytest.raises(ValueError, match="INTELLIGENCE_MARKET_EVENT_MUST_NOT_BIND_INSTRUMENT"):
        _event(scope=IntelligenceScope.MARKET)


def test_fixture_provider_proves_api_is_not_required() -> None:
    event = _event()
    request = IntelligenceRequest(
        source="FIXTURE",
        start=event.available_time - timedelta(minutes=1),
        end=event.available_time + timedelta(minutes=1),
    )

    class FixtureProvider:
        source = "FIXTURE"

        def fetch_events(self, requested: IntelligenceRequest) -> ProviderIntelligenceBatch:
            return ProviderIntelligenceBatch(
                source=self.source,
                request=requested,
                events=(event,),
                fetched_at=requested.end,
            )

    provider: MarketIntelligenceProvider = FixtureProvider()
    assert fetch_intelligence_events(provider, request) == (event,)
