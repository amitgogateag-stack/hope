from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from hope.application.backtests.counterfactual import replay_rejected_entry_acceptance
from hope.application.backtests.engine import DeterministicBacktest
from hope.domain.execution.models import Environment, OrderSide
from hope.domain.execution.simulator import CostModel
from hope.domain.market_data.models import MarketBar
from hope.domain.provenance.models import ProvenanceRecord
from hope.domain.research import CounterfactualAcceptancePolicy
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.domain.signal.models import Signal, SignalType


UTC = timezone.utc
INSTRUMENT = "11111111-1111-1111-1111-111111111111"
SOURCE_SIGNAL_ID = UUID("22222222-2222-2222-2222-222222222222")


def make_bar(
    at: datetime,
    close: str,
    *,
    available_time: datetime | None = None,
) -> MarketBar:
    price = Decimal(close)
    visible_at = at if available_time is None else available_time
    return MarketBar(
        instrument_id=INSTRUMENT,
        event_time=at,
        available_time=visible_at,
        ingestion_time=visible_at,
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


def make_provenance(
    *,
    strategy_version: str = "counterfactual-test",
    cost_model_version: str = "test",
    environment: Environment = Environment.BACKTEST,
) -> ProvenanceRecord:
    return ProvenanceRecord(
        dataset_version="dataset-v1",
        universe_version="universe-v1",
        strategy_version=strategy_version,
        parameter_snapshot_hash="b" * 64,
        cost_model_version=cost_model_version,
        execution_model_version="deterministic-backtest-v1",
        code_commit="677da051d4b2c586f9df958c602c6b74e3021911",
        configuration_hash="c" * 64,
        environment=environment,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
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
    provenance = make_provenance(cost_model_version="counterfactual-costs")

    replay = replay_rejected_entry_acceptance(
        decision,
        bars,
        make_policy(),
        OrderSide.BUY,
        Decimal("10000"),
        cost_model,
        provenance=provenance,
    )
    repeated = replay_rejected_entry_acceptance(
        decision,
        bars,
        make_policy(),
        OrderSide.BUY,
        Decimal("10000"),
        cost_model,
        provenance=provenance,
    )

    assert primary.events == ()
    assert primary.final_state.cash == Decimal("10000")
    assert primary.final_state.positions == {}

    assert replay.source_signal_id == SOURCE_SIGNAL_ID
    assert replay.counterfactual_signal_id != SOURCE_SIGNAL_ID
    assert replay.counterfactual_signal_id == repeated.counterfactual_signal_id
    assert replay.source_risk.reason_code == "PRIMARY_RISK_REJECT"
    assert replay.provenance == provenance
    assert repeated.provenance == provenance
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

    horizon_evidence = replay.horizon_valuation
    assert horizon_evidence.mark_bar.event_time == replay.horizon
    assert horizon_evidence.mark_bar.close == Decimal("105")
    assert horizon_evidence.valuation.as_of == replay.horizon
    assert horizon_evidence.valuation.market_value == Decimal("210")
    assert horizon_evidence.valuation.equity == Decimal("10005.77598")
    assert horizon_evidence.valuation.unrealized_pnl == Decimal("7.798")
    assert horizon_evidence.valuation.commissions == Decimal("2.02202")
    assert horizon_evidence.valuation.total_pnl == Decimal("5.77598")
    assert len(replay.replay.events) == 1


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
            provenance=make_provenance(),
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
        provenance=make_provenance(),
        execution_latency=timedelta(minutes=3),
    )

    assert replay.replay.events == ()
    assert len(replay.replay.unfilled_order_ids) == 1
    assert replay.replay.final_state.cash == Decimal("10000")
    assert replay.replay.final_state.positions == {}
    assert replay.horizon_valuation.valuation.as_of == replay.horizon
    assert replay.horizon_valuation.valuation.equity == Decimal("10000")
    assert replay.horizon_valuation.valuation.total_pnl == Decimal("0")


def test_horizon_valuation_uses_latest_pit_visible_mark_without_synthetic_exit():
    first = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    horizon = first + timedelta(minutes=2)
    bars = (
        make_bar(first, "100"),
        make_bar(first + timedelta(minutes=1), "101"),
        make_bar(
            horizon,
            "500",
            available_time=horizon + timedelta(minutes=1),
        ),
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
        provenance=make_provenance(),
    )

    assert replay.horizon == horizon
    assert replay.horizon_valuation.mark_bar.event_time == first + timedelta(minutes=1)
    assert replay.horizon_valuation.mark_bar.close == Decimal("101")
    assert replay.horizon_valuation.valuation.as_of == horizon
    assert replay.horizon_valuation.valuation.equity == Decimal("10000")
    assert replay.horizon_valuation.valuation.total_pnl == Decimal("0")
    assert len(replay.replay.events) == 1
    assert replay.replay.final_state.positions[UUID(INSTRUMENT)].quantity == Decimal("2")


@pytest.mark.parametrize(
    ("provenance", "error_code"),
    (
        (
            make_provenance(strategy_version="wrong-strategy"),
            "COUNTERFACTUAL_PROVENANCE_STRATEGY_MISMATCH",
        ),
        (
            make_provenance(cost_model_version="wrong-cost-model"),
            "COUNTERFACTUAL_PROVENANCE_COST_MODEL_MISMATCH",
        ),
        (
            make_provenance(environment=Environment.PAPER),
            "COUNTERFACTUAL_PROVENANCE_ENVIRONMENT_MUST_BE_BACKTEST",
        ),
    ),
)
def test_counterfactual_replay_rejects_mismatched_provenance(provenance, error_code):
    first = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    bars = (make_bar(first, "100"), make_bar(first + timedelta(minutes=1), "101"))
    signal = make_signal(first)
    _primary, decision = source_decision(bars, signal, RiskDecision.REJECT)

    with pytest.raises(ValueError, match=error_code):
        replay_rejected_entry_acceptance(
            decision,
            bars,
            make_policy(),
            OrderSide.BUY,
            Decimal("10000"),
            CostModel(version="test"),
            provenance=provenance,
        )
