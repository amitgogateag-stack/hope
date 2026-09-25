import os
from datetime import datetime, timedelta, timezone
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
            base_time = datetime.now(timezone.utc)
            event_time = base_time - timedelta(minutes=2)
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

            decision_time = base_time + timedelta(minutes=1)
            before = assessments.entry_gate_context(
                instrument_id,
                as_of=decision_time,
            )
            assert before.blocker_assessment_ids == (assessment_id,)

            future_resolution_time = decision_time + timedelta(minutes=5)
            connection.execute(
                text(
                    "INSERT INTO market_intelligence_review_resolutions("
                    "resolution_id,assessment_id,policy_version,outcome,rationale,created_at"
                    ") VALUES (:rid,:aid,:policy,'CLEARED',:rationale,:created_at)"
                ),
                {
                    "rid": uuid4(),
                    "aid": assessment_id,
                    "policy": assessment.policy_version,
                    "rationale": "Primary filing reviewed; no entry block required",
                    "created_at": future_resolution_time,
                },
            )

            still_blocked = assessments.entry_gate_context(
                instrument_id,
                as_of=decision_time,
            )
            assert still_blocked.blocker_assessment_ids == (assessment_id,)

            after = assessments.entry_gate_context(
                instrument_id,
                as_of=future_resolution_time,
            )
            assert after.blocker_assessment_ids == ()

            second_event = MarketIntelligenceEvent(
                event_id=uuid4(),
                scope=IntelligenceScope.COMPANY,
                instrument_id=instrument_id,
                source="FIXTURE",
                source_item_id=f"entry-gate-future-assessment-{uuid4()}",
                source_tier=IntelligenceSourceTier.PRIMARY_REGULATORY_OR_EXCHANGE,
                category=IntelligenceCategory.REGULATORY,
                materiality=IntelligenceMateriality.HIGH,
                recommended_action=IntelligenceAction.BLOCK_NEW_ENTRY,
                event_time=event_time,
                available_time=event_time,
                ingestion_time=event_time,
                source_payload_hash="f" * 64,
            )
            assert SqlAlchemyMarketIntelligenceRepository(connection).persist(second_event) is True
            second_assessment = assess_intelligence_event(second_event)
            second_assessment_id = uuid4()
            second_assessment_time = future_resolution_time + timedelta(minutes=5)
            connection.execute(
                text(
                    "INSERT INTO market_intelligence_assessments("
                    "assessment_id,event_id,policy_version,disposition,source_action,created_at"
                    ") VALUES (:aid,:eid,:policy,:disposition,:source_action,:created_at)"
                ),
                {
                    "aid": second_assessment_id,
                    "eid": second_assessment.event_id,
                    "policy": second_assessment.policy_version,
                    "disposition": second_assessment.disposition.value,
                    "source_action": second_assessment.source_action.value,
                    "created_at": second_assessment_time,
                },
            )

            before_second_assessment = assessments.entry_gate_context(
                instrument_id,
                as_of=future_resolution_time,
            )
            assert before_second_assessment.blocker_assessment_ids == ()

            after_second_assessment = assessments.entry_gate_context(
                instrument_id,
                as_of=second_assessment_time,
            )
            assert after_second_assessment.blocker_assessment_ids == (
                second_assessment_id,
            )
        finally:
            transaction.rollback()


@pytest.mark.integration
def test_market_scope_intelligence_block_applies_to_any_instrument_until_cleared() -> None:
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
                {"iid": instrument_id, "symbol": f"MARKET-BLOCK-{str(instrument_id)[:8]}"},
            )

            base_time = datetime.now(timezone.utc)
            event = MarketIntelligenceEvent(
                event_id=uuid4(),
                scope=IntelligenceScope.MARKET,
                source="FIXTURE",
                source_item_id=f"market-block-{uuid4()}",
                source_tier=IntelligenceSourceTier.PRIMARY_REGULATORY_OR_EXCHANGE,
                category=IntelligenceCategory.MACRO,
                materiality=IntelligenceMateriality.CRITICAL,
                recommended_action=IntelligenceAction.MARKET_RISK_HALT_CANDIDATE,
                event_time=base_time - timedelta(minutes=2),
                available_time=base_time - timedelta(minutes=1),
                ingestion_time=base_time - timedelta(minutes=1),
                source_payload_hash="9" * 64,
            )
            assert SqlAlchemyMarketIntelligenceRepository(connection).persist(event) is True
            assessment = assess_intelligence_event(event)
            assessment_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO market_intelligence_assessments("
                    "assessment_id,event_id,policy_version,disposition,source_action,created_at"
                    ") VALUES (:aid,:eid,:policy,:disposition,:source_action,:created_at)"
                ),
                {
                    "aid": assessment_id,
                    "eid": assessment.event_id,
                    "policy": assessment.policy_version,
                    "disposition": assessment.disposition.value,
                    "source_action": assessment.source_action.value,
                    "created_at": base_time,
                },
            )

            assessments = SqlAlchemyIntelligenceAssessmentRepository(connection)
            blocked = assessments.entry_gate_context(
                instrument_id,
                as_of=base_time + timedelta(minutes=1),
            )
            assert blocked.blocker_assessment_ids == (assessment_id,)

            connection.execute(
                text(
                    "INSERT INTO market_intelligence_review_resolutions("
                    "resolution_id,assessment_id,policy_version,outcome,rationale,created_at"
                    ") VALUES (:rid,:aid,:policy,'CLEARED',:rationale,:created_at)"
                ),
                {
                    "rid": uuid4(),
                    "aid": assessment_id,
                    "policy": assessment.policy_version,
                    "rationale": "Market-wide risk review cleared",
                    "created_at": base_time + timedelta(minutes=2),
                },
            )

            still_blocked = assessments.entry_gate_context(
                instrument_id,
                as_of=base_time + timedelta(minutes=1),
            )
            assert still_blocked.blocker_assessment_ids == (assessment_id,)

            cleared = assessments.entry_gate_context(
                instrument_id,
                as_of=base_time + timedelta(minutes=2),
            )
            assert cleared.blocker_assessment_ids == ()
        finally:
            transaction.rollback()


@pytest.mark.integration
def test_position_risk_review_does_not_block_new_entry_gate() -> None:
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
                {"iid": instrument_id, "symbol": f"POSITION-REVIEW-{str(instrument_id)[:8]}"},
            )

            base_time = datetime.now(timezone.utc)
            event = MarketIntelligenceEvent(
                event_id=uuid4(),
                scope=IntelligenceScope.COMPANY,
                instrument_id=instrument_id,
                source="FIXTURE",
                source_item_id=f"position-review-{uuid4()}",
                source_tier=IntelligenceSourceTier.PRIMARY_REGULATORY_OR_EXCHANGE,
                category=IntelligenceCategory.REGULATORY,
                materiality=IntelligenceMateriality.HIGH,
                recommended_action=IntelligenceAction.REDUCE_RISK_CANDIDATE,
                event_time=base_time - timedelta(minutes=2),
                available_time=base_time - timedelta(minutes=1),
                ingestion_time=base_time - timedelta(minutes=1),
                source_payload_hash="8" * 64,
            )
            assert SqlAlchemyMarketIntelligenceRepository(connection).persist(event) is True
            assessment = assess_intelligence_event(event)
            assessment_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO market_intelligence_assessments("
                    "assessment_id,event_id,policy_version,disposition,source_action,created_at"
                    ") VALUES (:aid,:eid,:policy,:disposition,:source_action,:created_at)"
                ),
                {
                    "aid": assessment_id,
                    "eid": assessment.event_id,
                    "policy": assessment.policy_version,
                    "disposition": assessment.disposition.value,
                    "source_action": assessment.source_action.value,
                    "created_at": base_time,
                },
            )

            gate = SqlAlchemyIntelligenceAssessmentRepository(connection).entry_gate_context(
                instrument_id,
                as_of=base_time + timedelta(minutes=1),
            )
            assert gate.blocker_assessment_ids == ()
        finally:
            transaction.rollback()


@pytest.mark.integration
def test_data_review_required_blocks_new_entry_until_reviewed() -> None:
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
                {"iid": instrument_id, "symbol": f"DATA-REVIEW-{str(instrument_id)[:8]}"},
            )

            base_time = datetime.now(timezone.utc)
            event = MarketIntelligenceEvent(
                event_id=uuid4(),
                scope=IntelligenceScope.COMPANY,
                instrument_id=instrument_id,
                source="SECONDARY_FIXTURE",
                source_item_id=f"data-review-{uuid4()}",
                source_tier=IntelligenceSourceTier.SECONDARY_OR_SOCIAL,
                category=IntelligenceCategory.OTHER,
                materiality=IntelligenceMateriality.MEDIUM,
                recommended_action=IntelligenceAction.DATA_REVIEW_REQUIRED,
                event_time=base_time - timedelta(minutes=2),
                available_time=base_time - timedelta(minutes=1),
                ingestion_time=base_time - timedelta(minutes=1),
                source_payload_hash="7" * 64,
            )
            assert SqlAlchemyMarketIntelligenceRepository(connection).persist(event) is True
            assessment = assess_intelligence_event(event)
            assessment_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO market_intelligence_assessments("
                    "assessment_id,event_id,policy_version,disposition,source_action,created_at"
                    ") VALUES (:aid,:eid,:policy,:disposition,:source_action,:created_at)"
                ),
                {
                    "aid": assessment_id,
                    "eid": assessment.event_id,
                    "policy": assessment.policy_version,
                    "disposition": assessment.disposition.value,
                    "source_action": assessment.source_action.value,
                    "created_at": base_time,
                },
            )

            assessments = SqlAlchemyIntelligenceAssessmentRepository(connection)
            blocked = assessments.entry_gate_context(
                instrument_id,
                as_of=base_time + timedelta(minutes=1),
            )
            assert blocked.blocker_assessment_ids == (assessment_id,)

            connection.execute(
                text(
                    "INSERT INTO market_intelligence_review_resolutions("
                    "resolution_id,assessment_id,policy_version,outcome,rationale,created_at"
                    ") VALUES (:rid,:aid,:policy,'NO_ACTION_REQUIRED',:rationale,:created_at)"
                ),
                {
                    "rid": uuid4(),
                    "aid": assessment_id,
                    "policy": assessment.policy_version,
                    "rationale": "Secondary-source item reviewed; no entry action required",
                    "created_at": base_time + timedelta(minutes=2),
                },
            )

            still_blocked = assessments.entry_gate_context(
                instrument_id,
                as_of=base_time + timedelta(minutes=1),
            )
            assert still_blocked.blocker_assessment_ids == (assessment_id,)

            cleared = assessments.entry_gate_context(
                instrument_id,
                as_of=base_time + timedelta(minutes=2),
            )
            assert cleared.blocker_assessment_ids == ()
        finally:
            transaction.rollback()


@pytest.mark.integration
def test_block_confirmed_review_keeps_entry_gate_closed() -> None:
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
                {"iid": instrument_id, "symbol": f"BLOCK-CONFIRMED-{str(instrument_id)[:8]}"},
            )

            base_time = datetime.now(timezone.utc)
            event = MarketIntelligenceEvent(
                event_id=uuid4(),
                scope=IntelligenceScope.COMPANY,
                instrument_id=instrument_id,
                source="FIXTURE",
                source_item_id=f"block-confirmed-{uuid4()}",
                source_tier=IntelligenceSourceTier.PRIMARY_REGULATORY_OR_EXCHANGE,
                category=IntelligenceCategory.REGULATORY,
                materiality=IntelligenceMateriality.HIGH,
                recommended_action=IntelligenceAction.BLOCK_NEW_ENTRY,
                event_time=base_time - timedelta(minutes=2),
                available_time=base_time - timedelta(minutes=1),
                ingestion_time=base_time - timedelta(minutes=1),
                source_payload_hash="6" * 64,
            )
            assert SqlAlchemyMarketIntelligenceRepository(connection).persist(event) is True
            assessment = assess_intelligence_event(event)
            assessment_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO market_intelligence_assessments("
                    "assessment_id,event_id,policy_version,disposition,source_action,created_at"
                    ") VALUES (:aid,:eid,:policy,:disposition,:source_action,:created_at)"
                ),
                {
                    "aid": assessment_id,
                    "eid": assessment.event_id,
                    "policy": assessment.policy_version,
                    "disposition": assessment.disposition.value,
                    "source_action": assessment.source_action.value,
                    "created_at": base_time,
                },
            )

            confirmation_time = base_time + timedelta(minutes=2)
            connection.execute(
                text(
                    "INSERT INTO market_intelligence_review_resolutions("
                    "resolution_id,assessment_id,policy_version,outcome,rationale,created_at"
                    ") VALUES (:rid,:aid,:policy,'BLOCK_CONFIRMED',:rationale,:created_at)"
                ),
                {
                    "rid": uuid4(),
                    "aid": assessment_id,
                    "policy": assessment.policy_version,
                    "rationale": "Primary evidence confirms the entry block remains required",
                    "created_at": confirmation_time,
                },
            )

            assessments = SqlAlchemyIntelligenceAssessmentRepository(connection)
            before_confirmation = assessments.entry_gate_context(
                instrument_id,
                as_of=base_time + timedelta(minutes=1),
            )
            assert before_confirmation.blocker_assessment_ids == (assessment_id,)

            after_confirmation = assessments.entry_gate_context(
                instrument_id,
                as_of=confirmation_time,
            )
            assert after_confirmation.blocker_assessment_ids == (assessment_id,)
        finally:
            transaction.rollback()


@pytest.mark.integration
def test_entry_gate_does_not_use_intelligence_before_system_ingestion_time() -> None:
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
                {"iid": instrument_id, "symbol": f"INGESTION-PIT-{str(instrument_id)[:8]}"},
            )

            base_time = datetime.now(timezone.utc)
            ingestion_time = base_time + timedelta(minutes=5)
            event = MarketIntelligenceEvent(
                event_id=uuid4(),
                scope=IntelligenceScope.COMPANY,
                instrument_id=instrument_id,
                source="FIXTURE",
                source_item_id=f"ingestion-pit-{uuid4()}",
                source_tier=IntelligenceSourceTier.PRIMARY_REGULATORY_OR_EXCHANGE,
                category=IntelligenceCategory.REGULATORY,
                materiality=IntelligenceMateriality.HIGH,
                recommended_action=IntelligenceAction.BLOCK_NEW_ENTRY,
                event_time=base_time - timedelta(minutes=2),
                available_time=base_time - timedelta(minutes=1),
                ingestion_time=ingestion_time,
                source_payload_hash="5" * 64,
            )
            assert SqlAlchemyMarketIntelligenceRepository(connection).persist(event) is True
            assessment = assess_intelligence_event(event)
            assessment_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO market_intelligence_assessments("
                    "assessment_id,event_id,policy_version,disposition,source_action,created_at"
                    ") VALUES (:aid,:eid,:policy,:disposition,:source_action,:created_at)"
                ),
                {
                    "aid": assessment_id,
                    "eid": assessment.event_id,
                    "policy": assessment.policy_version,
                    "disposition": assessment.disposition.value,
                    "source_action": assessment.source_action.value,
                    "created_at": base_time,
                },
            )

            assessments = SqlAlchemyIntelligenceAssessmentRepository(connection)
            before_ingestion = assessments.entry_gate_context(
                instrument_id,
                as_of=base_time + timedelta(minutes=1),
            )
            assert before_ingestion.blocker_assessment_ids == ()

            after_ingestion = assessments.entry_gate_context(
                instrument_id,
                as_of=ingestion_time,
            )
            assert after_ingestion.blocker_assessment_ids == (assessment_id,)
        finally:
            transaction.rollback()


@pytest.mark.integration
def test_intelligence_assessment_cannot_predate_event_ingestion() -> None:
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
                {"iid": instrument_id, "symbol": f"ASSESS-INGEST-{str(instrument_id)[:8]}"},
            )

            base_time = datetime.now(timezone.utc)
            ingestion_time = base_time + timedelta(minutes=5)
            event = MarketIntelligenceEvent(
                event_id=uuid4(),
                scope=IntelligenceScope.COMPANY,
                instrument_id=instrument_id,
                source="FIXTURE",
                source_item_id=f"assessment-ingestion-{uuid4()}",
                source_tier=IntelligenceSourceTier.PRIMARY_REGULATORY_OR_EXCHANGE,
                category=IntelligenceCategory.REGULATORY,
                materiality=IntelligenceMateriality.HIGH,
                recommended_action=IntelligenceAction.BLOCK_NEW_ENTRY,
                event_time=base_time - timedelta(minutes=2),
                available_time=base_time - timedelta(minutes=1),
                ingestion_time=ingestion_time,
                source_payload_hash="4" * 64,
            )
            assert SqlAlchemyMarketIntelligenceRepository(connection).persist(event) is True
            assessment = assess_intelligence_event(event)

            with pytest.raises(
                IntegrityError,
                match="INTELLIGENCE_ASSESSMENT_PRECEDES_EVENT_INGESTION",
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO market_intelligence_assessments("
                            "assessment_id,event_id,policy_version,disposition,source_action,created_at"
                            ") VALUES (:aid,:eid,:policy,:disposition,:source_action,:created_at)"
                        ),
                        {
                            "aid": uuid4(),
                            "eid": assessment.event_id,
                            "policy": assessment.policy_version,
                            "disposition": assessment.disposition.value,
                            "source_action": assessment.source_action.value,
                            "created_at": base_time,
                        },
                    )
        finally:
            transaction.rollback()
