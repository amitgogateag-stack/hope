from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.application.backtests.certified_execution import (
    CertifiedBacktestDefinition,
    certified_backtest_handler,
)
from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.execution import CertifiedResearchInputs
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

    definition = CertifiedBacktestDefinition(
        strategy_factory=strategy_factory,
        risk_factory=risk_factory,
        cost_model_factory=lambda resolved_plan: CostModel("research-cost-v1"),
        side_factory=lambda resolved_plan: OrderSide.BUY,
        initial_cash_factory=lambda resolved_plan: Decimal("100000"),
    )
    result = certified_backtest_handler(definition)(plan, inputs)
    assert result.initial_cash == Decimal("100000")
    assert len(result.valuations) == 1
    assert seen == [("strategy", plan), ("risk", plan)]


def test_certified_backtest_rejects_invalid_compiled_components() -> None:
    plan = _plan()
    inputs = _inputs()
    valid = {
        "strategy_factory": lambda resolved_plan: (lambda context: None),
        "risk_factory": lambda resolved_plan: (lambda signal: None),
        "cost_model_factory": lambda resolved_plan: CostModel("research-cost-v1"),
        "side_factory": lambda resolved_plan: OrderSide.BUY,
        "initial_cash_factory": lambda resolved_plan: Decimal("100000"),
    }
    cases = (
        ({"strategy_factory": lambda resolved_plan: object()}, "STRATEGY_MUST_BE_CALLABLE"),
        ({"risk_factory": lambda resolved_plan: object()}, "RISK_MUST_BE_CALLABLE"),
        ({"cost_model_factory": lambda resolved_plan: object()}, "COST_MODEL_REQUIRED"),
        ({"side_factory": lambda resolved_plan: "BUY"}, "SIDE_REQUIRED"),
        ({"initial_cash_factory": lambda resolved_plan: 100000}, "INITIAL_CASH_MUST_BE_DECIMAL"),
        ({"initial_cash_factory": lambda resolved_plan: Decimal("NaN")}, "INITIAL_CASH_MUST_BE_FINITE"),
        ({"initial_cash_factory": lambda resolved_plan: Decimal("-1")}, "INITIAL_CASH_MUST_BE_NON_NEGATIVE"),
    )
    for changes, error in cases:
        definition = CertifiedBacktestDefinition(**(valid | changes))
        with pytest.raises((TypeError, ValueError), match=error):
            certified_backtest_handler(definition)(plan, inputs)


def test_certified_backtest_definition_rejects_noncallable_factories() -> None:
    valid = {
        "strategy_factory": lambda resolved_plan: (lambda context: None),
        "risk_factory": lambda resolved_plan: (lambda signal: None),
        "cost_model_factory": lambda resolved_plan: CostModel("research-cost-v1"),
        "side_factory": lambda resolved_plan: OrderSide.BUY,
        "initial_cash_factory": lambda resolved_plan: Decimal("100000"),
    }
    for field in tuple(valid):
        values = dict(valid)
        values[field] = object()
        with pytest.raises(TypeError, match="FACTORY_MUST_BE_CALLABLE"):
            CertifiedBacktestDefinition(**values)


def test_certified_backtest_rejects_substituted_inputs_and_definition() -> None:
    plan = _plan()
    inputs = _inputs()
    definition = CertifiedBacktestDefinition(
        strategy_factory=lambda resolved_plan: (lambda context: None),
        risk_factory=lambda resolved_plan: (lambda signal: None),
        cost_model_factory=lambda resolved_plan: CostModel("research-cost-v1"),
        side_factory=lambda resolved_plan: OrderSide.BUY,
        initial_cash_factory=lambda resolved_plan: Decimal("100000"),
    )
    with pytest.raises(TypeError, match="DEFINITION_REQUIRED"):
        certified_backtest_handler(object())
    handler = certified_backtest_handler(definition)
    with pytest.raises(TypeError, match="REQUIRES_EXECUTION_PLAN"):
        handler(object(), inputs)
    with pytest.raises(TypeError, match="REQUIRES_CERTIFIED_INPUTS"):
        handler(plan, object())
