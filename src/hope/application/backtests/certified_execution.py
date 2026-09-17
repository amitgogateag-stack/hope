from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Callable

from hope.application.backtests.engine import DeterministicBacktest, RiskFn, StrategyFn
from hope.application.experiments.execution import CertifiedResearchInputs
from hope.application.market_data.calendar import MarketSessionCalendar
from hope.domain.execution.models import OrderSide
from hope.domain.execution.simulator import CostModel
from hope.infrastructure.repositories.execution_provenance import CertifiedExecutionPlan


StrategyFactory = Callable[[CertifiedExecutionPlan], StrategyFn]
RiskFactory = Callable[[CertifiedExecutionPlan], RiskFn]
CostModelFactory = Callable[[CertifiedExecutionPlan], CostModel]
SideFactory = Callable[[CertifiedExecutionPlan], OrderSide]
InitialCashFactory = Callable[[CertifiedExecutionPlan], Decimal]
ExecutionSettingsFactory = Callable[[CertifiedExecutionPlan], "CertifiedBacktestExecutionSettings"]


@dataclass(frozen=True)
class CertifiedBacktestExecutionSettings:
    """Explicit deterministic engine mechanics compiled from certified plan provenance."""

    execution_latency: timedelta = timedelta(0)
    order_submission_delay: timedelta = timedelta(0)
    max_fill_quantity: Decimal | None = None
    expected_interval: timedelta | None = None
    session_calendar: MarketSessionCalendar | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.execution_latency, timedelta):
            raise TypeError("CERTIFIED_BACKTEST_EXECUTION_LATENCY_MUST_BE_TIMEDELTA")
        if not isinstance(self.order_submission_delay, timedelta):
            raise TypeError("CERTIFIED_BACKTEST_ORDER_SUBMISSION_DELAY_MUST_BE_TIMEDELTA")
        if self.execution_latency < timedelta(0):
            raise ValueError("CERTIFIED_BACKTEST_EXECUTION_LATENCY_MUST_BE_NON_NEGATIVE")
        if self.order_submission_delay < timedelta(0):
            raise ValueError("CERTIFIED_BACKTEST_ORDER_SUBMISSION_DELAY_MUST_BE_NON_NEGATIVE")
        if self.max_fill_quantity is not None:
            if not isinstance(self.max_fill_quantity, Decimal):
                raise TypeError("CERTIFIED_BACKTEST_MAX_FILL_QUANTITY_MUST_BE_DECIMAL")
            if not self.max_fill_quantity.is_finite():
                raise ValueError("CERTIFIED_BACKTEST_MAX_FILL_QUANTITY_MUST_BE_FINITE")
            if self.max_fill_quantity <= 0:
                raise ValueError("CERTIFIED_BACKTEST_MAX_FILL_QUANTITY_MUST_BE_POSITIVE")
        if self.expected_interval is not None:
            if not isinstance(self.expected_interval, timedelta):
                raise TypeError("CERTIFIED_BACKTEST_EXPECTED_INTERVAL_MUST_BE_TIMEDELTA")
            if self.expected_interval <= timedelta(0):
                raise ValueError("CERTIFIED_BACKTEST_EXPECTED_INTERVAL_MUST_BE_POSITIVE")
        if self.session_calendar is not None and not isinstance(
            self.session_calendar, MarketSessionCalendar
        ):
            raise TypeError("CERTIFIED_BACKTEST_SESSION_CALENDAR_REQUIRED")


@dataclass(frozen=True)
class CertifiedBacktestDefinition:
    """Registered code that compiles one certified execution plan into a deterministic backtest."""

    strategy_factory: StrategyFactory
    risk_factory: RiskFactory
    cost_model_factory: CostModelFactory
    side_factory: SideFactory
    initial_cash_factory: InitialCashFactory
    execution_settings_factory: ExecutionSettingsFactory

    def __post_init__(self) -> None:
        for name, factory in (
            ("STRATEGY", self.strategy_factory),
            ("RISK", self.risk_factory),
            ("COST_MODEL", self.cost_model_factory),
            ("SIDE", self.side_factory),
            ("INITIAL_CASH", self.initial_cash_factory),
            ("EXECUTION_SETTINGS", self.execution_settings_factory),
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
        execution_settings = definition.execution_settings_factory(plan)

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
        if not isinstance(execution_settings, CertifiedBacktestExecutionSettings):
            raise TypeError("CERTIFIED_BACKTEST_EXECUTION_SETTINGS_REQUIRED")

        expected_instruments = tuple(
            str(instrument_id)
            for instrument_id in inputs.universe_snapshot.active_instrument_ids(
                inputs.market_context.as_of
            )
        )
        backtest = DeterministicBacktest(
            initial_cash,
            cost_model,
            execution_latency=execution_settings.execution_latency,
            order_submission_delay=execution_settings.order_submission_delay,
            max_fill_quantity=execution_settings.max_fill_quantity,
        )
        return backtest.run(
            inputs.market_context.bars,
            strategy,
            risk,
            side,
            expected_instrument_ids=expected_instruments,
            expected_interval=execution_settings.expected_interval,
            session_calendar=execution_settings.session_calendar,
        )

    return execute
