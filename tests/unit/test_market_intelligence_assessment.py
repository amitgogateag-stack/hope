from datetime import datetime, timezone
from uuid import uuid4

from hope.domain.market_intelligence.assessment import (
    IntelligenceDisposition,
    assess_intelligence_event,
)
from hope.domain.market_intelligence.models import (
    IntelligenceAction,
    IntelligenceCategory,
    IntelligenceMateriality,
    IntelligenceScope,
    IntelligenceSourceTier,
    MarketIntelligenceEvent,
)


def _event(action: IntelligenceAction) -> MarketIntelligenceEvent:
    t = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)
    return MarketIntelligenceEvent(
        event_id=uuid4(),
        scope=IntelligenceScope.COMPANY,
        instrument_id=uuid4(),
        source="FIXTURE",
        source_item_id=str(uuid4()),
        source_tier=IntelligenceSourceTier.PRIMARY_REGULATORY_OR_EXCHANGE,
        category=IntelligenceCategory.REGULATORY,
        materiality=IntelligenceMateriality.HIGH,
        recommended_action=action,
        event_time=t,
        available_time=t,
        ingestion_time=t,
        source_payload_hash="a" * 64,
    )


def test_intelligence_assessment_never_returns_order_instruction() -> None:
    assert assess_intelligence_event(
        _event(IntelligenceAction.EXIT_CANDIDATE)
    ).disposition is IntelligenceDisposition.POSITION_RISK_REVIEW
    assert assess_intelligence_event(
        _event(IntelligenceAction.BLOCK_NEW_ENTRY)
    ).disposition is IntelligenceDisposition.ENTRY_ELIGIBILITY_REVIEW
    assert assess_intelligence_event(
        _event(IntelligenceAction.MARKET_RISK_HALT_CANDIDATE)
    ).disposition is IntelligenceDisposition.MARKET_RISK_REVIEW
