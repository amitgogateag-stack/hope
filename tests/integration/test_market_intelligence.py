import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.domain.market_intelligence.assessment import (
    IntelligenceDisposition,
    assess_intelligence_event,
)
from hope.domain.market_intelligence.review import (
    IntelligenceReviewOutcome,
    IntelligenceReviewResolution,
)
from hope.domain.market_intelligence.models import (
    IntelligenceAction,
    IntelligenceCategory,
    IntelligenceMateriality,
    IntelligenceScope,
    IntelligenceSourceTier,
    MarketIntelligenceEvent,
)
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.market_intelligence import (
    SqlAlchemyMarketIntelligenceRepository,
)
from hope.infrastructure.repositories.market_intelligence_assessments import (
    SqlAlchemyIntelligenceAssessmentRepository,
)
from hope.infrastructure.repositories.market_intelligence_reviews import (
    SqlAlchemyIntelligenceReviewRepository,
)


@pytest.mark.integration
def test_market_intelligence_is_idempotent_immutable_and_pit_universe_bound() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            instrument_id, universe_id, universe_version_id = uuid4(), uuid4(), uuid4()
            connection.execute(
                text(
                    "INSERT INTO instruments(instrument_id,canonical_symbol,exchange,status) "
                    "VALUES (:iid,'TEST','TEST','ACTIVE')"
                ),
                {"iid": instrument_id},
            )
            connection.execute(
                text("INSERT INTO universes(universe_id,name) VALUES (:uid,:name)"),
                {"uid": universe_id, "name": f"intel-{universe_id}"},
            )
            connection.execute(
                text(
                    "INSERT INTO universe_versions("
                    "universe_version_id,universe_id,version,pit_certified,declared_member_count"
                    ") VALUES (:uvid,:uid,'v1',TRUE,1)"
                ),
                {"uvid": universe_version_id, "uid": universe_id},
            )
            connection.execute(
                text(
                    "INSERT INTO universe_members("
                    "universe_version_id,instrument_id,valid_from,valid_to"
                    ") VALUES (:uvid,:iid,:start,NULL)"
                ),
                {
                    "uvid": universe_version_id,
                    "iid": instrument_id,
                    "start": datetime(2026, 1, 1, tzinfo=timezone.utc),
                },
            )

            event = MarketIntelligenceEvent(
                event_id=uuid4(),
                scope=IntelligenceScope.COMPANY,
                instrument_id=instrument_id,
                universe_version_id=universe_version_id,
                source="FIXTURE",
                source_item_id="fixture-1",
                source_tier=IntelligenceSourceTier.PRIMARY_REGULATORY_OR_EXCHANGE,
                category=IntelligenceCategory.REGULATORY,
                materiality=IntelligenceMateriality.HIGH,
                recommended_action=IntelligenceAction.BLOCK_NEW_ENTRY,
                event_time=datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc),
                available_time=datetime(2026, 9, 18, 10, 1, tzinfo=timezone.utc),
                ingestion_time=datetime(2026, 9, 18, 10, 2, tzinfo=timezone.utc),
                source_payload_hash="b" * 64,
            )

            repository = SqlAlchemyMarketIntelligenceRepository(connection)
            assert repository.persist(event) is True
            assert repository.persist(event) is False
            stored = repository.get(event.event_id)
            assert stored is not None
            assert stored.recommended_action is IntelligenceAction.BLOCK_NEW_ENTRY

            with pytest.raises(IntegrityError, match="MARKET_INTELLIGENCE_EVENT_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE market_intelligence_events "
                            "SET materiality='LOW' WHERE event_id=:id"
                        ),
                        {"id": event.event_id},
                    )

            outside_instrument_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO instruments(instrument_id,canonical_symbol,exchange,status) "
                    "VALUES (:iid,'OUTSIDE','TEST','ACTIVE')"
                ),
                {"iid": outside_instrument_id},
            )
            with pytest.raises(
                IntegrityError,
                match="INTELLIGENCE_INSTRUMENT_NOT_IN_PIT_UNIVERSE",
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO market_intelligence_events("
                            "event_id,scope,instrument_id,universe_version_id,source,"
                            "source_item_id,source_tier,category,materiality,recommended_action,"
                            "event_time,available_time,ingestion_time,source_payload_hash"
                            ") VALUES (:eid,'COMPANY',:iid,:uvid,'FIXTURE','outside',1,"
                            "'REGULATORY','HIGH','BLOCK_NEW_ENTRY',:t,:t,:t,:hash)"
                        ),
                        {
                            "eid": uuid4(),
                            "iid": outside_instrument_id,
                            "uvid": universe_version_id,
                            "t": datetime(2026, 9, 18, 10, 3, tzinfo=timezone.utc),
                            "hash": "c" * 64,
                        },
                    )
        finally:
            transaction.rollback()



@pytest.mark.integration
def test_intelligence_assessment_is_bound_idempotent_and_immutable() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            event = MarketIntelligenceEvent(
                event_id=uuid4(),
                scope=IntelligenceScope.MARKET,
                source="FIXTURE",
                source_item_id=f"market-{uuid4()}",
                source_tier=IntelligenceSourceTier.PRIMARY_REGULATORY_OR_EXCHANGE,
                category=IntelligenceCategory.MACRO,
                materiality=IntelligenceMateriality.CRITICAL,
                recommended_action=IntelligenceAction.MARKET_RISK_HALT_CANDIDATE,
                event_time=datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc),
                available_time=datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc),
                ingestion_time=datetime(2026, 9, 18, 10, 1, tzinfo=timezone.utc),
                source_payload_hash="d" * 64,
            )
            SqlAlchemyMarketIntelligenceRepository(connection).persist(event)

            assessment = assess_intelligence_event(event)
            assessment_id = uuid4()
            repository = SqlAlchemyIntelligenceAssessmentRepository(connection)
            assert repository.persist(assessment_id, assessment) is True
            assert repository.persist(uuid4(), assessment) is False

            stored = repository.get_for_event(
                event.event_id,
                policy_version=assessment.policy_version,
            )
            assert stored is not None
            assert stored.disposition is IntelligenceDisposition.MARKET_RISK_REVIEW

            with pytest.raises(
                IntegrityError,
                match="INTELLIGENCE_ASSESSMENT_SOURCE_ACTION_MISMATCH",
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO market_intelligence_assessments("
                            "assessment_id,event_id,policy_version,disposition,source_action"
                            ") VALUES (:aid,:eid,'wrong-policy','OBSERVE_ONLY','OBSERVE')"
                        ),
                        {"aid": uuid4(), "eid": event.event_id},
                    )

            with pytest.raises(
                IntegrityError,
                match="INTELLIGENCE_ASSESSMENT_DISPOSITION_MISMATCH",
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO market_intelligence_assessments("
                            "assessment_id,event_id,policy_version,disposition,source_action"
                            ") VALUES (:aid,:eid,'wrong-disposition','OBSERVE_ONLY',"
                            "'MARKET_RISK_HALT_CANDIDATE')"
                        ),
                        {"aid": uuid4(), "eid": event.event_id},
                    )

            with pytest.raises(
                IntegrityError,
                match="MARKET_INTELLIGENCE_ASSESSMENT_IMMUTABLE",
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE market_intelligence_assessments "
                            "SET disposition='OBSERVE_ONLY' WHERE assessment_id=:aid"
                        ),
                        {"aid": assessment_id},
                    )
        finally:
            transaction.rollback()



@pytest.mark.integration
def test_intelligence_entry_gate_uses_pit_scope_and_review_resolution() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            instrument_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO instruments(instrument_id,canonical_symbol,exchange,status) "
                    "VALUES (:iid,:symbol,'TEST','ACTIVE')"
                ),
                {"iid": instrument_id, "symbol": f"INTEL-{str(instrument_id)[:8]}"},
            )
            event_time = datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc)
            event = MarketIntelligenceEvent(
                event_id=uuid4(),
                scope=IntelligenceScope.COMPANY,
                instrument_id=instrument_id,
                source="FIXTURE",
                source_item_id=f"entry-gate-{uuid4()}",
                source_tier=IntelligenceSourceTier.PRIMARY_REGULATORY_OR_EXCHANGE,
                category=IntelligenceCategory.REGULATORY,
                materiality=IntelligenceMateriality.HIGH,
                recommended_action=IntelligenceAction.BLOCK_NEW_ENTRY,
                event_time=event_time,
                available_time=event_time,
                ingestion_time=event_time,
                source_payload_hash="e" * 64,
            )
            SqlAlchemyMarketIntelligenceRepository(connection).persist(event)
            assessment = assess_intelligence_event(event)
            assessment_id = uuid4()
            assessments = SqlAlchemyIntelligenceAssessmentRepository(connection)
            assessments.persist(assessment_id, assessment)

            before = assessments.entry_gate_context(
                instrument_id,
                as_of=event_time,
            )
            assert before.blocker_assessment_ids == (assessment_id,)

            review = IntelligenceReviewResolution(
                assessment_id=assessment_id,
                policy_version=assessment.policy_version,
                outcome=IntelligenceReviewOutcome.CLEARED,
                rationale="Primary filing reviewed; no entry block required",
            )
            SqlAlchemyIntelligenceReviewRepository(connection).resolve(
                uuid4(),
                review,
            )
            after = assessments.entry_gate_context(
                instrument_id,
                as_of=event_time,
            )
            assert after.blocker_assessment_ids == ()
        finally:
            transaction.rollback()
