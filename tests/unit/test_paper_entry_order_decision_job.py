from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper.context import PaperCycleContext
from hope.application.paper.jobs import PaperEntryOrderDecisionJob
from hope.domain.execution.models import Environment, OrderSide
from hope.domain.market_intelligence.gate import IntelligenceEntryGateContext
from hope.domain.risk.inputs import PortfolioEntryRiskInputs
from hope.domain.risk.models import RiskDecision
from hope.domain.risk.portfolio import (
    PortfolioEntryRiskRequest,
    PortfolioRiskEngine,
    PortfolioRiskLimits,
    PortfolioRiskSnapshot,
)
from hope.domain.signal.models import Signal, SignalType


UTC = timezone.utc


class _Runtime:
    def __init__(self, job_run) -> None:
        self.cycle = PaperCycleContext(job_run)
        self.events = []

    def record_risk(self, assessment) -> bool:
        self.events.append(("risk", assessment))
        return True

    def record_order(self, order) -> bool:
        self.events.append(("order", order))
        return True


def _signal(*, signal_type: SignalType = SignalType.ENTRY) -> Signal:
    return Signal(
        signal_id=uuid4(),
        instrument_id=uuid4(),
        strategy_version="paper-entry-v1",
        decision_time=datetime(2026, 9, 13, 15, 0, tzinfo=UTC),
        signal_type=signal_type,
        conviction=Decimal("0.8"),
        inputs_hash="a" * 64,
    )


def _inputs(signal: Signal, *, proposed_quantity: Decimal = Decimal("2")) -> PortfolioEntryRiskInputs:
    return PortfolioEntryRiskInputs(
        request=PortfolioEntryRiskRequest(
            signal_id=signal.signal_id,
            instrument_id=signal.instrument_id,
            strategy_version=signal.strategy_version,
            proposed_quantity=proposed_quantity,
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


def _engine(*, max_position_notional: Decimal = Decimal("1000")) -> PortfolioRiskEngine:
    return PortfolioRiskEngine(
        PortfolioRiskLimits(
            max_position_notional=max_position_notional,
            max_gross_exposure=Decimal("5000"),
            max_open_positions=5,
        )
    )


def _runtime() -> _Runtime:
    return _Runtime(
        create_scheduled_job_run(
            "paper-entry-order",
            datetime(2026, 9, 13, 15, 1, tzinfo=UTC),
        )
    )


def test_paper_entry_order_job_records_approval_before_deterministic_order() -> None:
    signal = _signal()
    runtime = _runtime()

    PaperEntryOrderDecisionJob(
        signal,
        _inputs(signal),
        _engine(),
        OrderSide.BUY,
        IntelligenceEntryGateContext(),
    )(runtime)

    assert [kind for kind, _ in runtime.events] == ["risk", "order"]
    assessment = runtime.events[0][1]
    order = runtime.events[1][1]
    assert assessment.decision is RiskDecision.APPROVE
    assert order.order_id == runtime.cycle.order_id(signal.signal_id)
    assert order.signal_id == signal.signal_id
    assert order.instrument_id == signal.instrument_id
    assert order.quantity == Decimal("2")
    assert order.side is OrderSide.BUY
    assert order.environment is Environment.PAPER


def test_paper_entry_order_job_persists_rejection_without_order() -> None:
    signal = _signal()
    runtime = _runtime()

    PaperEntryOrderDecisionJob(
        signal,
        _inputs(signal, proposed_quantity=Decimal("20")),
        _engine(max_position_notional=Decimal("1000")),
        OrderSide.BUY,
        IntelligenceEntryGateContext(),
    )(runtime)

    assert len(runtime.events) == 1
    kind, assessment = runtime.events[0]
    assert kind == "risk"
    assert assessment.decision is RiskDecision.REJECT
    assert assessment.approved_quantity == Decimal("0")


def test_paper_entry_order_job_fails_closed_on_intelligence_entry_block() -> None:
    signal = _signal()
    runtime = _runtime()
    gate = IntelligenceEntryGateContext(blocker_assessment_ids=(uuid4(),))

    PaperEntryOrderDecisionJob(
        signal,
        _inputs(signal),
        _engine(),
        OrderSide.BUY,
        intelligence_gate=gate,
    )(runtime)

    assert len(runtime.events) == 1
    kind, assessment = runtime.events[0]
    assert kind == "risk"
    assert assessment.decision is RiskDecision.REJECT
    assert assessment.reason_code == "INTELLIGENCE_ENTRY_REVIEW_REQUIRED"
    assert assessment.approved_quantity == Decimal("0")


def test_paper_entry_order_job_rejects_non_entry_signal_before_any_effect() -> None:
    signal = _signal(signal_type=SignalType.EXIT)
    runtime = _runtime()
    job = PaperEntryOrderDecisionJob(
        signal,
        _inputs(signal),
        _engine(),
        OrderSide.SELL,
        IntelligenceEntryGateContext(),
    )

    with pytest.raises(ValueError, match="PORTFOLIO_ENTRY_RISK_REQUIRES_ENTRY_SIGNAL"):
        job(runtime)

    assert runtime.events == []


def test_paper_entry_order_job_rejects_risk_lineage_mismatch_before_any_effect() -> None:
    signal = _signal()
    other = _signal()
    runtime = _runtime()
    job = PaperEntryOrderDecisionJob(
        signal,
        _inputs(other),
        _engine(),
        OrderSide.BUY,
        IntelligenceEntryGateContext(),
    )

    with pytest.raises(ValueError, match="PORTFOLIO_RISK_SIGNAL_MISMATCH"):
        job(runtime)

    assert runtime.events == []


def test_paper_entry_order_job_requires_typed_dependencies() -> None:
    signal = _signal()
    inputs = _inputs(signal)
    engine = _engine()

    with pytest.raises(TypeError, match="PAPER_ENTRY_ORDER_JOB_REQUIRES_SIGNAL"):
        PaperEntryOrderDecisionJob(
            object(),
            inputs,
            engine,
            OrderSide.BUY,
            IntelligenceEntryGateContext(),
        )
    with pytest.raises(TypeError, match="PAPER_ENTRY_ORDER_JOB_REQUIRES_RISK_INPUTS"):
        PaperEntryOrderDecisionJob(
            signal,
            object(),
            engine,
            OrderSide.BUY,
            IntelligenceEntryGateContext(),
        )
    with pytest.raises(TypeError, match="PAPER_ENTRY_ORDER_JOB_REQUIRES_RISK_ENGINE"):
        PaperEntryOrderDecisionJob(
            signal,
            inputs,
            object(),
            OrderSide.BUY,
            IntelligenceEntryGateContext(),
        )
    with pytest.raises(TypeError, match="PAPER_ENTRY_ORDER_JOB_REQUIRES_ORDER_SIDE"):
        PaperEntryOrderDecisionJob(
            signal,
            inputs,
            engine,
            object(),
            IntelligenceEntryGateContext(),
        )
    with pytest.raises(
        TypeError,
        match="PAPER_ENTRY_ORDER_JOB_REQUIRES_INTELLIGENCE_GATE_CONTEXT",
    ):
        PaperEntryOrderDecisionJob(
            signal,
            inputs,
            engine,
            OrderSide.BUY,
            intelligence_gate=object(),
        )


def test_paper_entry_order_job_rejects_naive_signal_time_before_any_effect() -> None:
    signal = _signal().model_copy(
        update={"decision_time": datetime(2026, 9, 13, 15, 0)}
    )
    runtime = _runtime()
    job = PaperEntryOrderDecisionJob(
        signal,
        _inputs(signal),
        _engine(),
        OrderSide.BUY,
        IntelligenceEntryGateContext(),
    )

    with pytest.raises(
        ValueError,
        match="PAPER_ENTRY_RISK_SIGNAL_TIME_MUST_BE_TIMEZONE_AWARE",
    ):
        job(runtime)

    assert runtime.events == []


def test_paper_entry_order_job_rejects_future_signal_before_any_effect() -> None:
    signal = _signal().model_copy(
        update={"decision_time": datetime(2026, 9, 13, 15, 2, tzinfo=UTC)}
    )
    runtime = _runtime()
    job = PaperEntryOrderDecisionJob(signal, _inputs(signal), _engine(), OrderSide.BUY)

    with pytest.raises(ValueError, match="PAPER_ENTRY_RISK_SIGNAL_AFTER_JOB_SCHEDULE"):
        job(runtime)

    assert runtime.events == []
