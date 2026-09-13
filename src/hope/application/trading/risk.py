from __future__ import annotations

from hope.domain.risk.inputs import PortfolioEntryRiskInputs
from hope.domain.risk.models import RiskAssessment
from hope.domain.risk.portfolio import PortfolioRiskEngine
from hope.domain.signal.models import Signal, SignalType


def assess_portfolio_entry_signal(
    signal: Signal,
    inputs: PortfolioEntryRiskInputs,
    engine: PortfolioRiskEngine,
) -> RiskAssessment:
    """Assess an ENTRY signal using pre-derived authoritative portfolio risk inputs.

    This boundary refuses lineage mismatches before the generic trading kernel can
    create an order intent. Portfolio aggregates must already have been derived by
    the risk-input builder rather than supplied ad hoc here.
    """
    if not isinstance(signal, Signal):
        raise TypeError("SIGNAL_REQUIRED")
    if signal.signal_type is not SignalType.ENTRY:
        raise ValueError("PORTFOLIO_ENTRY_RISK_REQUIRES_ENTRY_SIGNAL")
    if not isinstance(inputs, PortfolioEntryRiskInputs):
        raise TypeError("PORTFOLIO_ENTRY_RISK_INPUTS_REQUIRED")
    if not isinstance(engine, PortfolioRiskEngine):
        raise TypeError("PORTFOLIO_RISK_ENGINE_REQUIRED")

    request = inputs.request
    if request.signal_id != signal.signal_id:
        raise ValueError("PORTFOLIO_RISK_SIGNAL_MISMATCH")
    if request.instrument_id != signal.instrument_id:
        raise ValueError("PORTFOLIO_RISK_INSTRUMENT_MISMATCH")
    if request.strategy_version != signal.strategy_version:
        raise ValueError("PORTFOLIO_RISK_STRATEGY_VERSION_MISMATCH")

    return engine.assess_entry(request, inputs.snapshot)
