from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.application.backtests.certified_execution import (
    CertifiedBacktestDefinition,
    CertifiedBacktestExecutionSettings,
    certified_backtest_handler,
)
from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.execution import CertifiedResearchInputs
from hope.application.market_data.calendar import MarketSessionCalendar
from hope.application.universe.snapshot import UniverseSnapshot
from hope.domain.execution.models import OrderSide
from hope.domain.execution.simulator import CostModel
from hope.domain.market_data.context import PITMarketContext
from hope.domain.market_data.models import MarketBar
from hope.domain.universe.models import UniverseMember, UniverseVersion
from hope.infrastructure.repositories.execution_provenance import CertifiedExecutionPlan


UTC = timezone.utc


def _plan() -> CertifiedExecutionPlan:
    configuration = {"strategy": {"lookback": 20}, "risk": {"max_positions": 3}}
    return CertifiedExecutionPlan(
        experiment_id="exp-certified-backtest",
        strategy_version_id=uuid4(),
        strategy_id=uuid4(),
        strategy_version="1.0.0",
        code_commit="abc123",
        configuration_hash=configuration_hash(configuration),
        configuration=configuration,
    )


def _inputs() -> CertifiedResearchInputs:
    instrument_id = uuid4()
    as_of = datetime(2026, 1, 2, tzinfo=UTC)
    bar = MarketBar(
        instrument_id=str(instrument_id),
        event_time=as_of,
        available_time=as_of,
        ingestion_time=as_of,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1000"),
    )
    snapshot = UniverseSnapshot(
        universe_version_id=uuid4(),
        version=UniverseVersion(
            universe_id=uuid4(),
            version="v1",
            declared_member_count=1,
            pit_certified=True,
        ),
        members=(UniverseMember(instrument_id=instrument_id),),
    )
    return CertifiedResearchInputs(
        market_context=PITMarketContext(as_of=as_of, bars=(bar,)),
        universe_snapshot=snapshot,
    )


def _valid_definition(**changes) -> CertifiedBacktestDefinition:
    values = {
        "strategy_factory": lambda resolved_plan: (lambda context: None),
        "risk_factory": lambda resolved_plan: (lambda signal: None),
        "cost_model_factory": lambda resolved_plan: CostModel("research-cost-v1"),
        "side_factory": lambda resolved_plan: OrderSide.BUY,
        "initial_cash_factory": lambda resolved_plan: Decimal("100000"),
        "execution_settings_factory": lambda resolved_plan: CertifiedBacktestExecutionSettings(),
    }
    values.update(changes)
    return CertifiedBacktestDefinition(**values)


def test_certified_backtest_consumes_exact_plan_and_frozen_market_inputs() -> None:
    plan = _plan()
    inputs = _inputs()
    seen = []

    def strategy_factory(resolved_plan):
        seen.append(("strategy", resolved_plan))
        return lambda context: None

    def risk_factory(resolved_plan):
        seen.append(("risk", resolved_plan))
        return lambda signal: (_ for _ in ()).throw(AssertionError("risk should not run without signals"))

    def settings_factory(resolved_plan):
        seen.append(("settings", resolved_plan))
        return CertifiedBacktestExecutionSettings()

    definition = _valid_definition(
        strategy_factory=strategy_factory,
        risk_factory=risk_factory,
        execution_settings_factory=settings_factory,
    )
    result = certified_backtest_handler(definition)(plan, inputs)
    assert result.initial_cash == Decimal("100000")
    assert len(result.valuations) == 1
    assert seen == [("strategy", plan), ("risk", plan), ("settings", plan)]


def test_certified_backtest_rejects_invalid_compiled_components() -> None:
    plan = _plan()
    inputs = _inputs()
    cases = (
        ({"strategy_factory": lambda resolved_plan: object()}, "STRATEGY_MUST_BE_CALLABLE"),
        ({"risk_factory": lambda resolved_plan: object()}, "RISK_MUST_BE_CALLABLE"),
        ({"cost_model_factory": lambda resolved_plan: object()}, "COST_MODEL_REQUIRED"),
        ({"side_factory": lambda resolved_plan: "BUY"}, "SIDE_REQUIRED"),
        ({"initial_cash_factory": lambda resolved_plan: 100000}, "INITIAL_CASH_MUST_BE_DECIMAL"),
        ({"initial_cash_factory": lambda resolved_plan: Decimal("NaN")}, "INITIAL_CASH_MUST_BE_FINITE"),
        ({"initial_cash_factory": lambda resolved_plan: Decimal("-1")}, "INITIAL_CASH_MUST_BE_NON_NEGATIVE"),
        ({"execution_settings_factory": lambda resolved_plan: object()}, "EXECUTION_SETTINGS_REQUIRED"),
    )
    for changes, error in cases:
        with pytest.raises((TypeError, ValueError), match=error):
            certified_backtest_handler(_valid_definition(**changes))(plan, inputs)


def test_certified_backtest_definition_rejects_noncallable_factories() -> None:
    valid = {
        "strategy_factory": lambda resolved_plan: (lambda context: None),
        "risk_factory": lambda resolved_plan: (lambda signal: None),
        "cost_model_factory": lambda resolved_plan: CostModel("research-cost-v1"),
        "side_factory": lambda resolved_plan: OrderSide.BUY,
        "initial_cash_factory": lambda resolved_plan: Decimal("100000"),
        "execution_settings_factory": lambda resolved_plan: CertifiedBacktestExecutionSettings(),
    }
    for field in tuple(valid):
        values = dict(valid)
        values[field] = object()
        with pytest.raises(TypeError, match="FACTORY_MUST_BE_CALLABLE"):
            CertifiedBacktestDefinition(**values)


def test_certified_backtest_execution_settings_fail_closed() -> None:
    cases = (
        ({"execution_latency": "0s"}, "EXECUTION_LATENCY_MUST_BE_TIMEDELTA"),
        ({"order_submission_delay": "0s"}, "ORDER_SUBMISSION_DELAY_MUST_BE_TIMEDELTA"),
        ({"execution_latency": timedelta(microseconds=-1)}, "EXECUTION_LATENCY_MUST_BE_NON_NEGATIVE"),
        ({"order_submission_delay": timedelta(microseconds=-1)}, "ORDER_SUBMISSION_DELAY_MUST_BE_NON_NEGATIVE"),
        ({"max_fill_quantity": 1}, "MAX_FILL_QUANTITY_MUST_BE_DECIMAL"),
        ({"max_fill_quantity": Decimal("NaN")}, "MAX_FILL_QUANTITY_MUST_BE_FINITE"),
        ({"max_fill_quantity": Decimal("0")}, "MAX_FILL_QUANTITY_MUST_BE_POSITIVE"),
        ({"expected_interval": "1m"}, "EXPECTED_INTERVAL_MUST_BE_TIMEDELTA"),
        ({"expected_interval": timedelta(0)}, "EXPECTED_INTERVAL_MUST_BE_POSITIVE"),
        ({"session_calendar": object()}, "SESSION_CALENDAR_REQUIRED"),
    )
    for changes, error in cases:
        with pytest.raises((TypeError, ValueError), match=error):
            CertifiedBacktestExecutionSettings(**changes)


def test_certified_backtest_applies_explicit_cadence_and_session_contract() -> None:
    plan = _plan()
    inputs = _inputs()
    as_of = inputs.market_context.as_of
    calendar = MarketSessionCalendar(((as_of, as_of + timedelta(hours=1)),))
    definition = _valid_definition(
        execution_settings_factory=lambda resolved_plan: CertifiedBacktestExecutionSettings(
            execution_latency=timedelta(seconds=1),
            order_submission_delay=timedelta(seconds=2),
            max_fill_quantity=Decimal("10"),
            expected_interval=timedelta(minutes=1),
            session_calendar=calendar,
        )
    )
    result = certified_backtest_handler(definition)(plan, inputs)
    assert result.initial_cash == Decimal("100000")
    assert len(result.valuations) == 1


def test_certified_backtest_rejects_substituted_inputs_and_definition() -> None:
    plan = _plan()
    inputs = _inputs()
    definition = _valid_definition()
    with pytest.raises(TypeError, match="DEFINITION_REQUIRED"):
        certified_backtest_handler(object())
    handler = certified_backtest_handler(definition)
    with pytest.raises(TypeError, match="REQUIRES_EXECUTION_PLAN"):
        handler(object(), inputs)
    with pytest.raises(TypeError, match="REQUIRES_CERTIFIED_INPUTS"):
        handler(plan, object())
