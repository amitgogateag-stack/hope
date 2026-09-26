from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.application.jobs import create_scheduled_job_run
from hope.domain.execution.models import OrderSide
from hope.domain.risk.inputs import PortfolioEntryRiskInputs
from hope.domain.risk.portfolio import (
    PortfolioEntryRiskRequest,
    PortfolioRiskEngine,
    PortfolioRiskLimits,
    PortfolioRiskSnapshot,
)
from hope.domain.market_data.context import PITMarketContext
from hope.domain.market_data.models import MarketBar
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.paper_runtime import (
    DurablePaperEntryOrderDecision,
    PaperMarketDataFreshnessPolicy,
    PaperJobDefinition,
    PaperJobRegistry,
    SqlAlchemyPaperRuntime,
    run_paper_once,
)


UTC = timezone.utc


def test_paper_job_definition_requires_canonical_key_and_callable_handler() -> None:
    with pytest.raises(ValueError, match="PAPER_JOB_KEY_REQUIRED"):
        PaperJobDefinition("", lambda runtime: None)
    with pytest.raises(ValueError, match="PAPER_JOB_KEY_NOT_CANONICAL"):
        PaperJobDefinition(" job ", lambda runtime: None)
    with pytest.raises(TypeError, match="PAPER_JOB_HANDLER_MUST_BE_CALLABLE"):
        PaperJobDefinition("job", object())


def test_paper_job_registry_requires_typed_definitions_and_rejects_duplicates() -> None:
    handler = lambda runtime: None
    with pytest.raises(TypeError, match="PAPER_JOB_DEFINITION_REQUIRED"):
        PaperJobRegistry([("job", handler)])
    with pytest.raises(ValueError, match="PAPER_JOB_KEY_DUPLICATE"):
        PaperJobRegistry(
            [
                PaperJobDefinition("job", handler),
                PaperJobDefinition("job", handler),
            ]
        )


def test_paper_job_registry_rejects_unregistered_job() -> None:
    job_run = create_scheduled_job_run("unknown", datetime(2026, 9, 10, 14, 0, tzinfo=UTC))
    with pytest.raises(RuntimeError, match="PAPER_JOB_NOT_REGISTERED"):
        PaperJobRegistry([]).resolve(job_run)


def test_concrete_paper_runtime_has_no_public_arbitrary_work_entrypoint() -> None:
    assert not hasattr(SqlAlchemyPaperRuntime, "run")


def test_run_paper_once_rejects_raw_callback_before_engine_use() -> None:
    job_run = create_scheduled_job_run("job", datetime(2026, 9, 10, 14, 1, tzinfo=UTC))
    with pytest.raises(TypeError, match="PAPER_ONE_SHOT_REQUIRES_JOB_REGISTRY"):
        run_paper_once(
            object(),
            job_run,
            lambda runtime: None,
            now=lambda: job_run.scheduled_for,
        )


def test_durable_paper_entry_rejects_unsupported_intelligence_policy() -> None:
    signal = Signal(
        signal_id=uuid4(),
        instrument_id=uuid4(),
        strategy_version="paper-policy-v1",
        decision_time=datetime(2026, 9, 25, 12, 0, tzinfo=UTC),
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"),
        inputs_hash="a" * 64,
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
    engine = PortfolioRiskEngine(
        PortfolioRiskLimits(
            max_position_notional=Decimal("1000"),
            max_gross_exposure=Decimal("5000"),
            max_open_positions=5,
        )
    )

    with pytest.raises(ValueError, match="PAPER_DURABLE_ENTRY_POLICY_UNSUPPORTED"):
        DurablePaperEntryOrderDecision(
            signal,
            risk_inputs,
            engine,
            OrderSide.BUY,
            policy_version="hope.intelligence-policy.v999",
        )


def _durable_entry_fixture():
    signal = Signal(
        signal_id=uuid4(),
        instrument_id=uuid4(),
        strategy_version="paper-durable-v1",
        decision_time=datetime(2026, 9, 26, 12, 0, tzinfo=UTC),
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"),
        inputs_hash="b" * 64,
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
    engine = PortfolioRiskEngine(
        PortfolioRiskLimits(
            max_position_notional=Decimal("1000"),
            max_gross_exposure=Decimal("5000"),
            max_open_positions=5,
        )
    )
    return signal, risk_inputs, engine


def test_durable_paper_entry_rejects_non_entry_signal_before_runtime_claim() -> None:
    signal, risk_inputs, engine = _durable_entry_fixture()
    exit_signal = signal.model_copy(update={"signal_type": SignalType.EXIT})

    with pytest.raises(ValueError, match="PAPER_DURABLE_ENTRY_REQUIRES_ENTRY_SIGNAL"):
        DurablePaperEntryOrderDecision(
            exit_signal,
            risk_inputs,
            engine,
            OrderSide.BUY,
        )


@pytest.mark.parametrize(
    ("field", "error"),
    [
        ("signal_id", "PAPER_DURABLE_ENTRY_RISK_SIGNAL_MISMATCH"),
        ("instrument_id", "PAPER_DURABLE_ENTRY_RISK_INSTRUMENT_MISMATCH"),
        ("strategy_version", "PAPER_DURABLE_ENTRY_RISK_STRATEGY_VERSION_MISMATCH"),
    ],
)
def test_durable_paper_entry_rejects_risk_lineage_mismatch_before_runtime_claim(
    field,
    error,
) -> None:
    signal, risk_inputs, engine = _durable_entry_fixture()
    replacement = {
        "signal_id": uuid4(),
        "instrument_id": uuid4(),
        "strategy_version": "other-strategy-v1",
    }[field]
    bad_request = risk_inputs.request.model_copy(update={field: replacement})
    bad_inputs = PortfolioEntryRiskInputs(
        request=bad_request,
        snapshot=risk_inputs.snapshot,
    )

    with pytest.raises(ValueError, match=error):
        DurablePaperEntryOrderDecision(
            signal,
            bad_inputs,
            engine,
            OrderSide.BUY,
        )


@pytest.mark.parametrize("max_age", [timedelta(0), timedelta(seconds=-1)])
def test_paper_market_data_freshness_policy_requires_positive_age(max_age) -> None:
    with pytest.raises(ValueError, match="PAPER_MARKET_DATA_FRESHNESS_MUST_BE_POSITIVE"):
        PaperMarketDataFreshnessPolicy(max_age)


def _freshness_bar(instrument_id, event_time, *, ingestion_time=None):
    ingestion = ingestion_time or event_time
    return MarketBar(
        instrument_id=str(instrument_id),
        event_time=event_time,
        available_time=event_time,
        ingestion_time=ingestion,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1000"),
    )


def test_paper_market_data_freshness_policy_fails_closed_when_active_instrument_missing() -> None:
    as_of = datetime(2026, 9, 26, 14, 0, tzinfo=UTC)
    active = uuid4()
    other = uuid4()
    context = PITMarketContext(
        as_of=as_of,
        bars=(
            _freshness_bar(other, as_of - timedelta(minutes=1)),
        ),
    )

    with pytest.raises(RuntimeError, match="PAPER_MARKET_DATA_MISSING_ACTIVE_INSTRUMENT"):
        PaperMarketDataFreshnessPolicy(timedelta(minutes=5)).assert_context_fresh(
            context,
            (active,),
        )


def test_paper_market_data_freshness_policy_allows_exact_age_boundary() -> None:
    as_of = datetime(2026, 9, 26, 14, 0, tzinfo=UTC)
    active = uuid4()
    context = PITMarketContext(
        as_of=as_of,
        bars=(
            _freshness_bar(active, as_of - timedelta(minutes=5)),
        ),
    )

    PaperMarketDataFreshnessPolicy(timedelta(minutes=5)).assert_context_fresh(
        context,
        (active,),
    )


def test_paper_market_data_freshness_policy_rejects_just_beyond_boundary() -> None:
    as_of = datetime(2026, 9, 26, 14, 0, tzinfo=UTC)
    active = uuid4()
    context = PITMarketContext(
        as_of=as_of,
        bars=(
            _freshness_bar(active, as_of - timedelta(minutes=5, microseconds=1)),
        ),
    )

    with pytest.raises(RuntimeError, match="PAPER_MARKET_DATA_STALE"):
        PaperMarketDataFreshnessPolicy(timedelta(minutes=5)).assert_context_fresh(
            context,
            (active,),
        )
