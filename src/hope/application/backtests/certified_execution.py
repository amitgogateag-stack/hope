from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from hope.application.backtests.engine import DeterministicBacktest, RiskFn, StrategyFn
from hope.application.experiments.execution import CertifiedResearchInputs
from hope.domain.execution.models import OrderSide
from hope.domain.execution.simulator import CostModel
from hope.infrastructure.repositories.execution_provenance import CertifiedExecutionPlan


StrategyFactory = Callable[[CertifiedExecutionPlan], StrategyFn]
RiskFactory = Callable[[CertifiedExecutionPlan], RiskFn]
CostModelFactory = Callable[[CertifiedExecutionPlan], CostModel]
SideFactory = Callable[[CertifiedExecutionPlan], OrderSide]
InitialCashFactory = Callable[[CertifiedExecutionPlan], Decimal]


@dataclass(frozen=True)
class CertifiedBacktestDefinition:
    """Registered code that compiles one certified execution plan into a deterministic backtest."""

    strategy_factory: StrategyFactory
    risk_factory: RiskFactory
    cost_model_factory: CostModelFactory
    side_factory: SideFactory
    initial_cash_factory: InitialCashFactory

    def __post_init__(self) -> None:
        for name, factory in (
            ("STRATEGY", self.strategy_factory),
            ("RISK", self.risk_factory),
            ("COST_MODEL", self.cost_model_factory),
            ("SIDE", self.side_factory),
            ("INITIAL_CASH", self.initial_cash_factory),
        ):
            if not callable(factory):
                raise TypeError(f"CERTIFIED_BACKTEST_{name}_FACTORY_MUST_BE_CALLABLE")


def certified_backtest_handler(definition: CertifiedBacktestDefinition):
    """Build a research-registry handler backed only by certified plan/config and PIT inputs."""
    if not isinstance(definition, CertifiedBacktestDefinition):
        raise TypeError("CERTIFIED_BACKTEST_DEFINITION_REQUIRED")

    def execute(plan: CertifiedExecutionPlan, inputs: CertifiedResearchInputs):
        if not isinstance(plan, CertifiedExecutionPlan):
            raise TypeError("CERTIFIED_BACKTEST_REQUIRES_EXECUTION_PLAN")
        if not isinstance(inputs, CertifiedResearchInputs):
            raise TypeError("CERTIFIED_BACKTEST_REQUIRES_CERTIFIED_INPUTS")

        strategy = definition.strategy_factory(plan)
        risk = definition.risk_factory(plan)
        cost_model = definition.cost_model_factory(plan)
        side = definition.side_factory(plan)
        initial_cash = definition.initial_cash_factory(plan)

        if not callable(strategy):
            raise TypeError("CERTIFIED_BACKTEST_STRATEGY_MUST_BE_CALLABLE")
        if not callable(risk):
            raise TypeError("CERTIFIED_BACKTEST_RISK_MUST_BE_CALLABLE")
        if not isinstance(cost_model, CostModel):
            raise TypeError("CERTIFIED_BACKTEST_COST_MODEL_REQUIRED")
        if not isinstance(side, OrderSide):
            raise TypeError("CERTIFIED_BACKTEST_SIDE_REQUIRED")
        if not isinstance(initial_cash, Decimal):
            raise TypeError("CERTIFIED_BACKTEST_INITIAL_CASH_MUST_BE_DECIMAL")
        if not initial_cash.is_finite():
            raise ValueError("CERTIFIED_BACKTEST_INITIAL_CASH_MUST_BE_FINITE")
        if initial_cash < 0:
            raise ValueError("CERTIFIED_BACKTEST_INITIAL_CASH_MUST_BE_NON_NEGATIVE")

        expected_instruments = tuple(
            str(instrument_id)
            for instrument_id in inputs.universe_snapshot.active_instrument_ids(
                inputs.market_context.as_of
            )
        )
        backtest = DeterministicBacktest(initial_cash, cost_model)
        return backtest.run(
            inputs.market_context.bars,
            strategy,
            risk,
            side,
            expected_instrument_ids=expected_instruments,
        )

    return execute
