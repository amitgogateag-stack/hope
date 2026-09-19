from decimal import Decimal
from uuid import uuid4

import pytest

from hope.application.trading.risk import assess_portfolio_entry_signal
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


def make_signal(**overrides) -> Signal:
    from datetime import datetime, timezone

    values = {
        "signal_id": uuid4(),
        "instrument_id": uuid4(),
        "strategy_version": "s1",
        "decision_time": datetime(2026, 9, 13, 14, 0, tzinfo=timezone.utc),
        "signal_type": SignalType.ENTRY,
        "conviction": Decimal("0.8"),
        "inputs_hash": "a" * 64,
    }
    values.update(overrides)
    return Signal(**values)


def make_inputs(signal: Signal, **request_overrides) -> PortfolioEntryRiskInputs:
    request_values = {
        "signal_id": signal.signal_id,
        "instrument_id": signal.instrument_id,
        "strategy_version": signal.strategy_version,
        "proposed_quantity": Decimal("10"),
        "reference_price": Decimal("100"),
        "current_instrument_exposure": Decimal("0"),
        "current_strategy_exposure": Decimal("0"),
        "opens_new_position": True,
    }
    request_values.update(request_overrides)
    return PortfolioEntryRiskInputs(
        request=PortfolioEntryRiskRequest(**request_values),
        snapshot=PortfolioRiskSnapshot(
            gross_exposure=Decimal("0"),
            open_positions=0,
            current_daily_loss=Decimal("0"),
            current_drawdown=Decimal("0"),
        ),
    )


def engine(max_position_notional: str = "5000") -> PortfolioRiskEngine:
    return PortfolioRiskEngine(
        PortfolioRiskLimits(
            max_position_notional=Decimal(max_position_notional),
            max_gross_exposure=Decimal("10000"),
            max_open_positions=5,
        )
    )


def test_portfolio_entry_boundary_returns_engine_approval():
    signal = make_signal()
    assessment = assess_portfolio_entry_signal(signal, make_inputs(signal), engine())

    assert assessment.signal_id == signal.signal_id
    assert assessment.decision is RiskDecision.APPROVE
    assert assessment.approved_quantity == Decimal("10")


def test_portfolio_entry_boundary_preserves_engine_rejection():
    signal = make_signal()
    assessment = assess_portfolio_entry_signal(
        signal,
        make_inputs(signal, proposed_quantity=Decimal("60")),
        engine(max_position_notional="5000"),
    )

    assert assessment.decision is RiskDecision.REJECT
    assert assessment.approved_quantity == Decimal("0")


def test_portfolio_entry_boundary_rejects_signal_lineage_mismatch():
    signal = make_signal()
    with pytest.raises(ValueError, match="PORTFOLIO_RISK_SIGNAL_MISMATCH"):
        assess_portfolio_entry_signal(
            signal,
            make_inputs(signal, signal_id=uuid4()),
            engine(),
        )


def test_portfolio_entry_boundary_rejects_instrument_lineage_mismatch():
    signal = make_signal()
    with pytest.raises(ValueError, match="PORTFOLIO_RISK_INSTRUMENT_MISMATCH"):
        assess_portfolio_entry_signal(
            signal,
            make_inputs(signal, instrument_id=uuid4()),
            engine(),
        )


def test_portfolio_entry_boundary_rejects_strategy_lineage_mismatch():
    signal = make_signal()
    with pytest.raises(ValueError, match="PORTFOLIO_RISK_STRATEGY_VERSION_MISMATCH"):
        assess_portfolio_entry_signal(
            signal,
            make_inputs(signal, strategy_version="s2"),
            engine(),
        )


def test_portfolio_entry_boundary_rejects_exit_signal():
    signal = make_signal(signal_type=SignalType.EXIT)
    with pytest.raises(ValueError, match="PORTFOLIO_ENTRY_RISK_REQUIRES_ENTRY_SIGNAL"):
        assess_portfolio_entry_signal(signal, make_inputs(signal), engine())



def test_portfolio_entry_boundary_rejects_when_intelligence_review_blocks_entry():
    signal = make_signal()
    blocker_id = uuid4()
    assessment = assess_portfolio_entry_signal(
        signal,
        make_inputs(signal),
        engine(),
        intelligence_gate=IntelligenceEntryGateContext(
            blocker_assessment_ids=(blocker_id,)
        ),
    )

    assert assessment.decision is RiskDecision.REJECT
    assert assessment.reason_code == "INTELLIGENCE_ENTRY_REVIEW_REQUIRED"
    assert assessment.approved_quantity == Decimal("0")


def test_portfolio_entry_boundary_allows_empty_intelligence_gate():
    signal = make_signal()
    assessment = assess_portfolio_entry_signal(
        signal,
        make_inputs(signal),
        engine(),
        intelligence_gate=IntelligenceEntryGateContext(),
    )
    assert assessment.decision is RiskDecision.APPROVE
