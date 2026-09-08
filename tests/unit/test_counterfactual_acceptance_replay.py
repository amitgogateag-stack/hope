from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from hope.application.backtests.counterfactual import replay_rejected_entry_acceptance
from hope.application.backtests.engine import DeterministicBacktest
from hope.domain.execution.models import OrderSide
from hope.domain.execution.simulator import CostModel
from hope.domain.market_data.models import MarketBar
from hope.domain.research import CounterfactualAcceptancePolicy
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.domain.signal.models import Signal, SignalType


UTC = timezone.utc
INSTRUMENT = "11111111-1111-1111-1111-111111111111"
SOURCE_SIGNAL_ID = UUID("22222222-2222-2222-2222-222222222222")


def make_bar(at: datetime, close: str) -> MarketBar:
    price = Decimal(close)
    return MarketBar(
        instrument_id=INSTRUMENT,
        event_time=at,
        available_time=at,
        ingestion_time=at,
        open=price,
        high=price + Decimal("1"),
        low=price - Decimal("1"),
        close=price,
        volume=Decimal("1000"),
    )


def make_signal(at: datetime) -> Signal:
    return Signal(
        signal_id=SOURCE_SIGNAL_ID,
        instrument_id=UUID(INSTRUMENT),
        strategy_version="counterfactual-test",
        decision_time=at,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.5"),
        inputs_hash="a" * 64,
    )


def make_policy(holding_period: timedelta = timedelta(minutes=2)) -> CounterfactualAcceptancePolicy:
    return CounterfactualAcceptancePolicy(
        policy_id="accept-rejected-entry",
        version="1",
        quantity=Decimal("2"),
        holding_period=holding_period,
    )


def source_decision(bars, signal, decision: RiskDecision):
    assessment = RiskAssessment(
        signal_id=signal.signal_id,
        decision=decision,
        reason_code="PRIMARY_RISK_REJECT" if decision is RiskDecision.REJECT else "PRIMARY_APPROVE",
        approved_quantity=Decimal("0") if decision is RiskDecision.REJECT else Decimal("1"),
    )
    result = DeterministicBacktest(
        Decimal("10000"),
        CostModel(version="primary"),
    ).run(
        bars,
        lambda context: signal if context.as_of == signal.decision_time else None,
        lambda _signal: assessment,
        OrderSide.BUY,
    )
    return result, result.decisions[0]


def test_rejected_entry_is_replayed_in_isolated_primary_execution_engine():
    first = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    bars = (
        make_bar(first, "100"),
        make_bar(first + timedelta(minutes=1), "101"),
        make_bar(first + timedelta(minutes=2), "105"),
        make_bar(first + timedelta(minutes=3), "999"),
    )
    signal = make_signal(first)
    primary, decision = source_decision(bars, signal, RiskDecision.REJECT)
    cost_model = CostModel(
        version="counterfactual-costs",
        commission_rate=Decimal("0.01"),
        slippage_bps=Decimal("10"),
    )

    replay = replay_rejected_entry_acceptance(
        decision,
        bars,
        make_policy(),
        OrderSide.BUY,
        Decimal("10000"),
        cost_model,
    )
    repeated = replay_rejected_entry_acceptance(
        decision,
        bars,
        make_policy(),
        OrderSide.BUY,
        Decimal("10000"),
        cost_model,
    )

    assert primary.events == ()
    assert primary.final_state.cash == Decimal("10000")
    assert primary.final_state.positions == {}

    assert replay.source_signal_id == SOURCE_SIGNAL_ID
    assert replay.counterfactual_signal_id != SOURCE_SIGNAL_ID
    assert replay.counterfactual_signal_id == repeated.counterfactual_signal_id
    assert replay.source_risk.reason_code == "PRIMARY_RISK_REJECT"
    assert replay.horizon == first + timedelta(minutes=2)
    assert replay.cost_model_version == "counterfactual-costs"
    assert len(replay.replay.decisions) == 1
    assert replay.replay.decisions[0].signal.signal_id == replay.counterfactual_signal_id
    assert replay.replay.decisions[0].risk.decision is RiskDecision.APPROVE
    assert replay.replay.decisions[0].risk.approved_quantity == Decimal("2")

    assert len(replay.replay.events) == 1
    fill = replay.replay.events[0].result.fill
    repeated_fill = repeated.replay.events[0].result.fill
    assert fill is not None
    assert repeated_fill is not None
    assert fill.fill_id == repeated_fill.fill_id
    assert fill.signal_id == replay.counterfactual_signal_id
    assert fill.quantity == Decimal("2")
    assert fill.fill_time == first + timedelta(minutes=1)
    assert fill.cost_model_version == "counterfactual-costs"
    assert fill.commission > 0
    assert fill.slippage > 0
    assert replay.replay.final_state.positions[UUID(INSTRUMENT)].quantity == Decimal("2")
    assert all(event.event_time <= replay.horizon for event in replay.replay.events)


def test_counterfactual_replay_rejects_a_primary_approved_decision():
    first = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    bars = (make_bar(first, "100"), make_bar(first + timedelta(minutes=1), "101"))
    signal = make_signal(first)
    _primary, decision = source_decision(bars, signal, RiskDecision.APPROVE)

    with pytest.raises(ValueError, match="COUNTERFACTUAL_REQUIRES_RISK_REJECTION"):
        replay_rejected_entry_acceptance(
            decision,
            bars,
            make_policy(),
            OrderSide.BUY,
            Decimal("10000"),
            CostModel(version="test"),
        )


def test_counterfactual_does_not_fill_after_declared_horizon():
    first = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    bars = (
        make_bar(first, "100"),
        make_bar(first + timedelta(minutes=1), "101"),
        make_bar(first + timedelta(minutes=2), "102"),
        make_bar(first + timedelta(minutes=3), "103"),
    )
    signal = make_signal(first)
    _primary, decision = source_decision(bars, signal, RiskDecision.REJECT)

    replay = replay_rejected_entry_acceptance(
        decision,
        bars,
        make_policy(holding_period=timedelta(minutes=2)),
        OrderSide.BUY,
        Decimal("10000"),
        CostModel(version="test"),
        execution_latency=timedelta(minutes=3),
    )

    assert replay.replay.events == ()
    assert len(replay.replay.unfilled_order_ids) == 1
    assert replay.replay.final_state.cash == Decimal("10000")
    assert replay.replay.final_state.positions == {}
