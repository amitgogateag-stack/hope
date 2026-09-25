import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from hope.application.jobs import JobRunStatus, create_scheduled_job_run
from hope.application.paper import PaperCycleContext, PaperCycleOutcome
from hope.application.paper.jobs import (
    PaperFillAccountingJob,
    PaperSignalPersistenceJob,
)
from hope.domain.execution import Fill, OrderSide
from hope.domain.market_intelligence.assessment import assess_intelligence_event
from hope.domain.market_intelligence.models import (
    IntelligenceAction,
    IntelligenceCategory,
    IntelligenceMateriality,
    IntelligenceScope,
    IntelligenceSourceTier,
    MarketIntelligenceEvent,
)
from hope.domain.risk.inputs import PortfolioEntryRiskInputs
from hope.domain.risk.portfolio import (
    PortfolioEntryRiskRequest,
    PortfolioRiskEngine,
    PortfolioRiskLimits,
    PortfolioRiskSnapshot,
)
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.paper_runtime import (
    DurablePaperEntryOrderDecision,
    PaperJobDefinition,
    PaperJobRegistry,
    run_paper_once,
)
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.market_intelligence import (
    SqlAlchemyMarketIntelligenceRepository,
)
from hope.infrastructure.repositories.market_intelligence_assessments import (
    SqlAlchemyIntelligenceAssessmentRepository,
)

UTC = timezone.utc
INPUTS_HASH = "f" * 64


@pytest.mark.integration
def test_authoritative_paper_runtime_persists_signal_risk_order_and_fill_in_separate_runs() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    portfolio_id = uuid4()
    signal_run = create_scheduled_job_run(
        "paper-runtime-signal",
        datetime(2026, 9, 10, 6, 0, tzinfo=UTC),
    )
    risk_order_run = create_scheduled_job_run(
        "paper-runtime-risk-order",
        datetime(2026, 9, 10, 6, 1, tzinfo=UTC),
    )
    fill_run = create_scheduled_job_run(
        "paper-runtime-fill",
        datetime(2026, 9, 10, 6, 2, tzinfo=UTC),
    )

    signal_context = PaperCycleContext(signal_run)
    risk_order_context = PaperCycleContext(risk_order_run)
    fill_context = PaperCycleContext(fill_run)
    decision_time = datetime(2026, 9, 10, 5, 59, tzinfo=UTC)
    signal_id = signal_context.signal_id(
        instrument_id=instrument_id,
        strategy_version="paper-runtime-v1",
        decision_time=decision_time,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"),
        inputs_hash=INPUTS_HASH,
    )
    signal = Signal(
        signal_id=signal_id,
        instrument_id=instrument_id,
        strategy_version="paper-runtime-v1",
        decision_time=decision_time,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"),
        inputs_hash=INPUTS_HASH,
    )
    risk_inputs = PortfolioEntryRiskInputs(
        request=PortfolioEntryRiskRequest(
            signal_id=signal.signal_id,
            instrument_id=signal.instrument_id,
            strategy_version=signal.strategy_version,
            proposed_quantity=Decimal("1"),
            reference_price=Decimal("100"),
            current_instrument_exposure=Decimal("0"),
            current_strategy_exposure=Decimal("0"),
            opens_new_position=True,
        ),
        snapshot=PortfolioRiskSnapshot(
            gross_exposure=Decimal("0"),
            open_positions=0,
            current_daily_loss=Decimal("0"),
            current_drawdown=Decimal("0"),
        ),
    )
    risk_engine = PortfolioRiskEngine(
        PortfolioRiskLimits(
            max_position_notional=Decimal("1000"),
            max_gross_exposure=Decimal("5000"),
            max_open_positions=5,
        )
    )
    order_id = risk_order_context.order_id(signal_id)
    fill = Fill(
        fill_context.fill_id(signal_id, 0),
        order_id,
        signal_id,
        instrument_id,
        OrderSide.BUY,
        Decimal("1"),
        Decimal("100"),
        Decimal("0.25"),
        Decimal("0"),
        "paper-runtime-cost-v1",
        datetime(2026, 9, 10, 6, 2, tzinfo=UTC),
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text(
                "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                "VALUES (:id, 'PAPER-RUNTIME', 'TEST', 'ACTIVE')"
            ),
            {"id": instrument_id},
        )

    registry = PaperJobRegistry(
        [
            PaperJobDefinition(signal_run.job_key, PaperSignalPersistenceJob(signal)),
            PaperJobDefinition(
                risk_order_run.job_key,
                DurablePaperEntryOrderDecision(
                    signal,
                    risk_inputs,
                    risk_engine,
                    OrderSide.BUY,
                ),
            ),
            PaperJobDefinition(
                fill_run.job_key,
                PaperFillAccountingJob(
                    portfolio_id,
                    Decimal("1000"),
                    fill,
                    sequence=0,
                ),
            ),
        ]
    )
    now = lambda: fill_run.scheduled_for + timedelta(minutes=1)

    assert run_paper_once(engine, signal_run, registry, now=now) is PaperCycleOutcome.EXECUTED
    assert run_paper_once(engine, risk_order_run, registry, now=now) is PaperCycleOutcome.EXECUTED
    assert run_paper_once(engine, fill_run, registry, now=now) is PaperCycleOutcome.EXECUTED

    with engine.connect() as connection:
        jobs = SqlAlchemyJobRunRepository(connection)
        assert connection.execute(
            text("SELECT count(*) FROM signals WHERE signal_id=:id"),
            {"id": signal.signal_id},
        ).scalar_one() == 1
        risk_row = connection.execute(
            text(
                "SELECT decision, reason_code, approved_quantity "
                "FROM paper_risk_assessments WHERE signal_id=:id"
            ),
            {"id": signal.signal_id},
        ).mappings().one()
        assert risk_row["decision"] == "APPROVE"
        assert risk_row["reason_code"] == "PORTFOLIO_RISK_APPROVED"
        assert risk_row["approved_quantity"] == Decimal("1")
        assert connection.execute(
            text("SELECT count(*) FROM orders WHERE order_id=:id"),
            {"id": order_id},
        ).scalar_one() == 1
        assert connection.execute(
            text("SELECT count(*) FROM fills WHERE fill_id=:id"),
            {"id": fill.fill_id},
        ).scalar_one() == 1
        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_portfolio_fill_applications "
                "WHERE portfolio_id=:portfolio_id AND fill_id=:fill_id"
            ),
            {"portfolio_id": portfolio_id, "fill_id": fill.fill_id},
        ).scalar_one() == 1

        effect_jobs = dict(
            connection.execute(
                text(
                    "SELECT effect_type, job_run_id FROM paper_effects "
                    "WHERE entity_id IN (:signal_id, :order_id, :fill_id)"
                ),
                {
                    "signal_id": signal.signal_id,
                    "order_id": order_id,
                    "fill_id": fill.fill_id,
                },
            ).all()
        )
        assert effect_jobs["SIGNAL"] == signal_run.job_run_id
        assert effect_jobs["RISK"] == risk_order_run.job_run_id
        assert effect_jobs["ORDER"] == risk_order_run.job_run_id
        assert effect_jobs["FILL"] == fill_run.job_run_id

        for run in (signal_run, risk_order_run, fill_run):
            record = jobs.get_record(run.job_run_id)
            assert record is not None
            assert record.status is JobRunStatus.SUCCEEDED


@pytest.mark.integration
def test_authoritative_paper_entry_resolves_durable_intelligence_block_before_order() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    signal_run = create_scheduled_job_run(
        "paper-runtime-intel-signal",
        datetime(2026, 9, 10, 7, 0, tzinfo=UTC),
    )
    risk_order_run = create_scheduled_job_run(
        "paper-runtime-intel-risk-order",
        datetime(2026, 9, 10, 7, 1, tzinfo=UTC),
    )
    decision_time = datetime(2026, 9, 10, 6, 59, tzinfo=UTC)
    signal_context = PaperCycleContext(signal_run)
    signal_id = signal_context.signal_id(
        instrument_id=instrument_id,
        strategy_version="paper-runtime-intel-v1",
        decision_time=decision_time,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"),
        inputs_hash=INPUTS_HASH,
    )
    signal = Signal(
        signal_id=signal_id,
        instrument_id=instrument_id,
        strategy_version="paper-runtime-intel-v1",
        decision_time=decision_time,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"),
        inputs_hash=INPUTS_HASH,
    )
    risk_inputs = PortfolioEntryRiskInputs(
        request=PortfolioEntryRiskRequest(
            signal_id=signal.signal_id,
            instrument_id=signal.instrument_id,
            strategy_version=signal.strategy_version,
            proposed_quantity=Decimal("1"),
            reference_price=Decimal("100"),
            current_instrument_exposure=Decimal("0"),
            current_strategy_exposure=Decimal("0"),
            opens_new_position=True,
        ),
        snapshot=PortfolioRiskSnapshot(
            gross_exposure=Decimal("0"),
            open_positions=0,
            current_daily_loss=Decimal("0"),
            current_drawdown=Decimal("0"),
        ),
    )
    risk_engine = PortfolioRiskEngine(
        PortfolioRiskLimits(
            max_position_notional=Decimal("1000"),
            max_gross_exposure=Decimal("5000"),
            max_open_positions=5,
        )
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text(
                "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                "VALUES (:id, :symbol, 'TEST', 'ACTIVE')"
            ),
            {"id": instrument_id, "symbol": f"PAPER-INTEL-{str(instrument_id)[:8]}"},
        )
        event = MarketIntelligenceEvent(
            event_id=uuid4(),
            scope=IntelligenceScope.COMPANY,
            instrument_id=instrument_id,
            source="PAPER_RUNTIME_FIXTURE",
            source_item_id=f"block-{uuid4()}",
            source_tier=IntelligenceSourceTier.PRIMARY_REGULATORY_OR_EXCHANGE,
            category=IntelligenceCategory.REGULATORY,
            materiality=IntelligenceMateriality.HIGH,
            recommended_action=IntelligenceAction.BLOCK_NEW_ENTRY,
            event_time=decision_time - timedelta(minutes=2),
            available_time=decision_time - timedelta(minutes=1),
            ingestion_time=decision_time - timedelta(minutes=1),
            source_payload_hash="d" * 64,
        )
        assert SqlAlchemyMarketIntelligenceRepository(connection).persist(event) is True
        assessment = assess_intelligence_event(event)
        assessment_id = uuid4()
        assert SqlAlchemyIntelligenceAssessmentRepository(connection).persist(
            assessment_id,
            assessment,
        ) is True

    registry = PaperJobRegistry(
        [
            PaperJobDefinition(signal_run.job_key, PaperSignalPersistenceJob(signal)),
            PaperJobDefinition(
                risk_order_run.job_key,
                DurablePaperEntryOrderDecision(
                    signal,
                    risk_inputs,
                    risk_engine,
                    OrderSide.BUY,
                ),
            ),
        ]
    )
    now = lambda: risk_order_run.scheduled_for + timedelta(minutes=1)

    assert run_paper_once(engine, signal_run, registry, now=now) is PaperCycleOutcome.EXECUTED
    assert run_paper_once(engine, risk_order_run, registry, now=now) is PaperCycleOutcome.EXECUTED

    with engine.connect() as connection:
        risk_row = connection.execute(
            text(
                "SELECT decision, reason_code, approved_quantity "
                "FROM paper_risk_assessments WHERE signal_id=:id"
            ),
            {"id": signal.signal_id},
        ).mappings().one()
        assert risk_row["decision"] == "REJECT"
        assert risk_row["reason_code"] == "INTELLIGENCE_ENTRY_REVIEW_REQUIRED"
        assert risk_row["approved_quantity"] == Decimal("0")
        assert connection.execute(
            text("SELECT count(*) FROM orders WHERE signal_id=:id"),
            {"id": signal.signal_id},
        ).scalar_one() == 0
