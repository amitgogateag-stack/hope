"""Certified research-to-PAPER bridge; no broker or independent trading engine."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, timedelta
from decimal import Decimal
from typing import Callable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, text

from hope.application.experiments.comparisons import build_research_comparison
from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.evaluation_protocol import REQUIRED_EVALUATION_STAGES
from hope.application.experiments.verified_source import VerifiedCertifiedResearchSourceResolver
from hope.application.paper.jobs import assert_paper_market_data_fresh, validate_paper_strategy_signals
from hope.application.paper.provenance import paper_decision_inputs_hash
from hope.application.trading.risk import assess_portfolio_entry_signal
from hope.domain.execution import CostModel, Environment, ExecutionQuote, OrderSide, simulate_market_fill
from hope.domain.execution.timeline import ExecutionTimeline
from hope.domain.portfolio.ledger import PortfolioState
from hope.domain.research.models import ResearchDecision
from hope.domain.risk.inputs import build_portfolio_entry_risk_inputs
from hope.domain.risk.models import RiskDecision
from hope.domain.risk.portfolio import PortfolioRiskEngine, PortfolioRiskLimits
from hope.domain.signal.models import SignalType
from hope.domain.strategy.candidates import StrategyMarket
from hope.domain.strategy.models import ParameterSnapshot, Strategy
from hope.domain.trading.kernel import create_order_intent, materialize_order
from hope.infrastructure.repositories.execution_provenance import CertifiedExecutionPlanResolver, SqlAlchemyExecutionProvenanceRepository
from hope.infrastructure.repositories.experiments import SqlAlchemyExperimentRepository
from hope.infrastructure.repositories.market_contexts import PITMarketContextRepository
from hope.infrastructure.repositories.market_intelligence_assessments import SqlAlchemyIntelligenceAssessmentRepository
from hope.infrastructure.repositories.paper_eligibility import assert_current_eligibility, SqlAlchemyPaperStrategyAuthorizationRepository
from hope.infrastructure.repositories.paper_portfolio import SqlAlchemyPaperPortfolioRepository
from hope.infrastructure.repositories.research_comparisons import SqlAlchemyResearchComparisonRepository
from hope.infrastructure.repositories.research_decisions import SqlAlchemyResearchDecisionRepository
from hope.infrastructure.repositories.research_evaluation_plans import SqlAlchemyResearchEvaluationPlanRepository
from hope.infrastructure.repositories.research_evaluation_results import SqlAlchemyResearchEvaluationResultRepository
from hope.infrastructure.repositories.research_run_evidence import SqlAlchemyResearchRunEvidenceRepository
from hope.infrastructure.repositories.research_runs import SqlAlchemyResearchRunRepository
from hope.infrastructure.repositories.universe_snapshots import UniverseSnapshotRepository


class CertifiedPaperSettings(BaseModel):
    """Explicit immutable execution configuration stored with approved research."""
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    market: StrategyMarket
    portfolio_id: UUID
    initial_cash: Decimal = Field(ge=0)
    quantity: Decimal = Field(gt=0)
    side: OrderSide
    risk_limits: PortfolioRiskLimits
    cost_model_version: str = Field(min_length=1)
    commission_rate: Decimal = Field(ge=0)
    slippage_bps: Decimal = Field(ge=0)
    latency_milliseconds: int = Field(ge=0, le=86400000, strict=True)
    submission_delay_milliseconds: int = Field(ge=0, le=86400000, strict=True)
    market_data_max_age_seconds: int = Field(gt=0, le=604800, strict=True)


@dataclass(frozen=True)
class CertifiedPaperRiskContext:
    """Reviewed durable performance anchors, never inferred from missing history."""
    session_start_equity: Decimal
    peak_equity: Decimal
    current_strategy_exposure: Decimal
    concentration: object = None


@dataclass(frozen=True)
class CertifiedPaperDefinition:
    family: str
    code_commit: str
    strategy_factory: Callable
    parameters_factory: Callable
    risk_context_factory: Callable | None = None

    def __post_init__(self):
        if any(not isinstance(v, str) or not v or v != v.strip() for v in (self.family, self.code_commit)):
            raise ValueError("PAPER_CERTIFIED_IMPLEMENTATION_IDENTITY_REQUIRED")
        if not callable(self.strategy_factory) or not callable(self.parameters_factory):
            raise TypeError("PAPER_CERTIFIED_IMPLEMENTATION_FACTORIES_REQUIRED")
        if self.risk_context_factory is not None and not callable(self.risk_context_factory):
            raise TypeError("PAPER_CERTIFIED_RISK_CONTEXT_FACTORY_REQUIRED")


def resolve_certified_decision(connection: Connection, decision_id: str, strategy_version_id: UUID):
    """Reconstruct comparison from the persisted protocol, stages and both runs."""
    decision = SqlAlchemyResearchDecisionRepository(connection).get(decision_id)
    if decision is None or decision.decision is not ResearchDecision.SUPPORTED:
        raise RuntimeError("PAPER_CERTIFIED_SUPPORTED_DECISION_REQUIRED")
    experiments = SqlAlchemyExperimentRepository(connection)
    runs = SqlAlchemyResearchRunRepository(connection)
    evidence = SqlAlchemyResearchRunEvidenceRepository(connection)
    plans = CertifiedExecutionPlanResolver(SqlAlchemyExecutionProvenanceRepository(connection))
    source = VerifiedCertifiedResearchSourceResolver(experiment_repository=experiments, run_repository=runs,
        evidence_repository=evidence, execution_plan_resolver=plans,
        universe_snapshot_repository=UniverseSnapshotRepository(connection),
        market_context_repository=PITMarketContextRepository(connection))
    for run_id in (decision.control_run_id, decision.variant_run_id):
        source.resolve(run_id)
    experiment = experiments.get(decision.variant_experiment_id)
    if experiment is None or experiment.strategy_version_id != strategy_version_id:
        raise RuntimeError("PAPER_CERTIFIED_STRATEGY_VERSION_MISMATCH")
    plan = plans.resolve(experiment)
    protocol = SqlAlchemyResearchEvaluationPlanRepository(connection).get(experiment.experiment_id)
    stages = SqlAlchemyResearchEvaluationResultRepository(connection).list_by_run_pair(
        experiment.experiment_id, decision.control_run_id, decision.variant_run_id)
    if (protocol is None or len(stages) != len(REQUIRED_EVALUATION_STAGES)
            or {row.stage for row in stages} != set(REQUIRED_EVALUATION_STAGES)
            or any(row.protocol_hash != protocol.protocol_hash for row in stages)):
        raise RuntimeError("PAPER_CERTIFIED_EVALUATION_INCOMPLETE")
    control = evidence.get(decision.control_run_id)
    variant = evidence.get(decision.variant_run_id)
    rebuilt = build_research_comparison(variant_experiment_id=experiment.experiment_id,
        control_run_id=decision.control_run_id, variant_run_id=decision.variant_run_id,
        control_result_fingerprint=control.result_fingerprint, variant_result_fingerprint=variant.result_fingerprint,
        protocol_hash=protocol.protocol_hash, stage_results={row.stage: {"result_fingerprint": row.result_fingerprint, "result": row.canonical_result} for row in stages})
    comparison = SqlAlchemyResearchComparisonRepository(connection).get_by_run_pair(
        experiment.experiment_id, decision.control_run_id, decision.variant_run_id)
    if comparison is None or rebuilt != comparison.canonical_comparison:
        raise RuntimeError("PAPER_CERTIFIED_COMPARISON_MISMATCH")
    return decision, experiment, plan


@dataclass(frozen=True)
class CertifiedResearchPaperWork:
    strategy_version_id: UUID
    market: StrategyMarket
    classification_id: UUID
    research_decision_id: str
    dataset_version_id: UUID
    definition: CertifiedPaperDefinition

    def __post_init__(self):
        if any(not isinstance(v, UUID) for v in (self.strategy_version_id, self.classification_id, self.dataset_version_id)):
            raise TypeError("PAPER_CERTIFIED_UUID_IDENTITY_REQUIRED")
        if not isinstance(self.market, StrategyMarket) or not isinstance(self.definition, CertifiedPaperDefinition):
            raise TypeError("PAPER_CERTIFIED_BINDING_REQUIRED")

    def bind(self, connection: Connection):
        # run_paper_once invokes bind before its new claim, holding this outer
        # transaction through all effects. Terminal/reconciliation paths skip it.
        assert_current_eligibility(connection, self.strategy_version_id, self.market,
                                   self.classification_id, self.research_decision_id)
        decision, experiment, plan = resolve_certified_decision(connection, self.research_decision_id, self.strategy_version_id)
        family = connection.execute(text("SELECT s.family FROM strategies s JOIN strategy_versions v USING(strategy_id) WHERE v.strategy_version_id=:id"), {"id": self.strategy_version_id}).scalar_one()
        if family != self.definition.family or plan.code_commit != self.definition.code_commit:
            raise RuntimeError("PAPER_CERTIFIED_IMPLEMENTATION_MISMATCH")
        if not isinstance(plan.configuration, dict):
            raise RuntimeError("PAPER_CERTIFIED_CONFIGURATION_REQUIRED")
        settings = CertifiedPaperSettings.model_validate(plan.configuration.get("paper_execution"))
        if settings.market is not self.market:
            raise RuntimeError("PAPER_CERTIFIED_MARKET_MISMATCH")
        strategy, parameters = self.definition.strategy_factory(plan), self.definition.parameters_factory(plan)
        if not isinstance(strategy, Strategy) or strategy.version != plan.strategy_version:
            raise RuntimeError("PAPER_CERTIFIED_STRATEGY_IMPLEMENTATION_MISMATCH")
        if not isinstance(parameters, ParameterSnapshot) or parameters.model_dump(mode="json") != plan.configuration.get("parameters"):
            raise RuntimeError("PAPER_CERTIFIED_PARAMETERS_MISMATCH")
        if configuration_hash(plan.configuration) != plan.configuration_hash:
            raise RuntimeError("PAPER_CERTIFIED_CONFIGURATION_CHANGED")
        sources = connection.execute(text("SELECT d.source FROM datasets d JOIN dataset_versions v USING(dataset_id) WHERE v.dataset_version_id IN (:research,:paper)"),
            {"research": experiment.dataset_version_id, "paper": self.dataset_version_id}).scalars().all()
        if not sources or len(set(sources)) != 1 or (self.dataset_version_id != experiment.dataset_version_id and len(sources) != 2):
            raise RuntimeError("PAPER_CERTIFIED_DATASET_SOURCE_MISMATCH")

        def execute(runtime):
            run = runtime.cycle.job_run
            SqlAlchemyPaperStrategyAuthorizationRepository(connection).record_for_run(run, self.strategy_version_id,
                self.market, self.classification_id, self.research_decision_id)
            latency = timedelta(milliseconds=settings.latency_milliseconds)
            delay = timedelta(milliseconds=settings.submission_delay_milliseconds)
            decision_time = run.scheduled_for.astimezone(UTC) - latency - delay
            universe = UniverseSnapshotRepository(connection).get(experiment.universe_version_id)
            if universe is None:
                raise RuntimeError("PAPER_CERTIFIED_UNIVERSE_UNAVAILABLE")
            active = universe.active_instrument_ids(decision_time)
            if not active:
                raise RuntimeError("PAPER_CERTIFIED_UNIVERSE_EMPTY")
            exchanges = {StrategyMarket.USA: ("NYSE", "NASDAQ", "AMEX", "ARCA"), StrategyMarket.INDIA: ("NSE", "BSE")}
            rows = connection.execute(text("SELECT instrument_id,exchange,status FROM instruments WHERE instrument_id = ANY(:ids)"), {"ids": list(active)}).all()
            if len(rows) != len(active) or any(row.exchange not in exchanges[self.market] or row.status != "ACTIVE" for row in rows):
                raise RuntimeError("PAPER_CERTIFIED_INSTRUMENT_MARKET_MISMATCH")
            contexts = PITMarketContextRepository(connection)
            context = contexts.get(self.dataset_version_id, as_of=decision_time,
                universe_version_id=experiment.universe_version_id, instrument_ids=active)
            max_age = timedelta(seconds=settings.market_data_max_age_seconds)
            assert_paper_market_data_fresh(context, active, max_age)
            inputs_hash = paper_decision_inputs_hash(strategy, context, universe, parameters,
                dataset_version_id=self.dataset_version_id, market_data_max_age=max_age)
            signals = validate_paper_strategy_signals(strategy, context, universe, parameters, inputs_hash)
            if len(signals) > 1 or any(signal.signal_type is not SignalType.ENTRY for signal in signals):
                raise RuntimeError("PAPER_CERTIFIED_CYCLE_REQUIRES_SINGLE_ENTRY_OR_NO_TRADE")

            def outcome(kind, reason):
                connection.execute(text("INSERT INTO paper_certified_outcomes(job_run_id,source_run_id,dataset_version_id,universe_version_id,configuration_hash,inputs_hash,decision_time,outcome,reason_code) VALUES (:job,:source,:dataset,:universe,:configuration,:inputs,:time,:outcome,:reason)"),
                    dict(job=run.job_run_id, source=decision.variant_run_id, dataset=self.dataset_version_id,
                        universe=experiment.universe_version_id, configuration=plan.configuration_hash,
                        inputs=inputs_hash, time=decision_time, outcome=kind, reason=reason))

            # NO_TRADE records the scheduled cycle resolution (existing exact
            # identity contract); the certified outcome retains the earlier
            # strategy-input decision time used for latency and provenance.
            if not signals:
                runtime.record_no_trade(strategy_version=strategy.version, decision_time=run.scheduled_for, inputs_hash=inputs_hash)
                outcome("NO_TRADE", "NO_SIGNAL")
                return
            signal = signals[0]
            portfolio = SqlAlchemyPaperPortfolioRepository(connection).load_ledger(settings.portfolio_id, lock_for_update=True)
            if portfolio is not None and portfolio.initial_cash != settings.initial_cash:
                raise RuntimeError("PAPER_CERTIFIED_PORTFOLIO_IDENTITY_MISMATCH")
            state = portfolio.state if portfolio is not None else PortfolioState(settings.initial_cash, {})
            marks = {instrument: context.for_instrument(str(instrument))[-1].close for instrument in active}
            if any(instrument not in marks for instrument in state.positions):
                raise RuntimeError("PAPER_CERTIFIED_PORTFOLIO_MARKS_UNAVAILABLE")
            if self.definition.risk_context_factory is not None:
                anchors = self.definition.risk_context_factory(connection, plan, signal, state, marks)
            elif portfolio is None:
                anchors = CertifiedPaperRiskContext(settings.initial_cash, settings.initial_cash, Decimal(0))
            else:
                raise RuntimeError("PAPER_CERTIFIED_PERFORMANCE_ANCHORS_UNAVAILABLE")
            if not isinstance(anchors, CertifiedPaperRiskContext):
                raise RuntimeError("PAPER_CERTIFIED_PERFORMANCE_ANCHORS_UNAVAILABLE")
            risk_inputs = build_portfolio_entry_risk_inputs(signal_id=signal.signal_id, instrument_id=signal.instrument_id,
                strategy_version=signal.strategy_version, proposed_quantity=settings.quantity,
                reference_price=marks[signal.instrument_id], portfolio_state=state, marks=marks,
                current_strategy_exposure=anchors.current_strategy_exposure,
                session_start_equity=anchors.session_start_equity, peak_equity=anchors.peak_equity,
                concentration=anchors.concentration)
            gate = SqlAlchemyIntelligenceAssessmentRepository(connection).entry_gate_context(signal.instrument_id, as_of=decision_time,
                policy_version="hope.intelligence-policy.v1")
            assessment = assess_portfolio_entry_signal(signal, risk_inputs, PortfolioRiskEngine(settings.risk_limits), intelligence_gate=gate)
            if assessment.decision is not RiskDecision.APPROVE:
                runtime.record_no_trade(strategy_version=strategy.version, decision_time=run.scheduled_for, inputs_hash=inputs_hash)
                outcome("RISK_BLOCKED", assessment.reason_code)
                return
            fill_context = contexts.get(self.dataset_version_id, as_of=run.scheduled_for,
                universe_version_id=experiment.universe_version_id, instrument_ids=active)
            quotes = [bar for bar in fill_context.for_instrument(str(signal.instrument_id))
                      if bar.event_time.astimezone(UTC) == run.scheduled_for.astimezone(UTC)]
            if len(quotes) != 1:
                raise RuntimeError("PAPER_CERTIFIED_EXECUTABLE_QUOTE_UNAVAILABLE")
            bar = quotes[0]
            quote = ExecutionQuote(signal.instrument_id, bar.event_time, bar.close, bar.close, bar.available_time)
            intent = create_order_intent(signal, assessment, settings.side, Environment.PAPER)
            order = materialize_order(intent, runtime.cycle.order_id(signal.signal_id))
            fill = simulate_market_fill(order, quote, runtime.cycle.fill_id(signal.signal_id, 0),
                CostModel(settings.cost_model_version, settings.commission_rate, settings.slippage_bps),
                timeline=ExecutionTimeline.from_decision(decision_time, latency=latency, order_submission_delay=delay).with_fill_time(run.scheduled_for))
            runtime.record_signal(signal)
            runtime.record_risk(assessment)
            runtime.record_order(order)
            runtime.record_fill(settings.portfolio_id, settings.initial_cash, fill, sequence=0)
            outcome("FILLED", "PORTFOLIO_RISK_APPROVED")
        return execute
