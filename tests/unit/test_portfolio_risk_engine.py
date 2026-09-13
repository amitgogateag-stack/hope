from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from hope.domain.risk.models import RiskDecision
from hope.domain.risk.portfolio import (
    PortfolioEntryRiskRequest,
    PortfolioRiskEngine,
    PortfolioRiskLimits,
    PortfolioRiskReason,
    PortfolioRiskSnapshot,
)


SIGNAL_ID = UUID("11111111-1111-1111-1111-111111111111")
INSTRUMENT_ID = UUID("22222222-2222-2222-2222-222222222222")


def limits() -> PortfolioRiskLimits:
    return PortfolioRiskLimits(
        max_position_notional=Decimal("25000"),
        max_gross_exposure=Decimal("100000"),
        max_open_positions=5,
    )


def request(**overrides) -> PortfolioEntryRiskRequest:
    values = {
        "signal_id": SIGNAL_ID,
        "instrument_id": INSTRUMENT_ID,
        "proposed_quantity": Decimal("10"),
        "reference_price": Decimal("1000"),
        "current_instrument_exposure": Decimal("0"),
        "opens_new_position": True,
    }
    values.update(overrides)
    return PortfolioEntryRiskRequest(**values)


def snapshot(**overrides) -> PortfolioRiskSnapshot:
    values = {"gross_exposure": Decimal("50000"), "open_positions": 2}
    values.update(overrides)
    return PortfolioRiskSnapshot(**values)


def test_portfolio_risk_approves_request_within_all_limits():
    assessment = PortfolioRiskEngine(limits()).assess_entry(request(), snapshot())

    assert assessment.decision is RiskDecision.APPROVE
    assert assessment.reason_code == PortfolioRiskReason.APPROVED
    assert assessment.approved_quantity == Decimal("10")


def test_portfolio_risk_rejects_resulting_position_notional_over_limit():
    assessment = PortfolioRiskEngine(limits()).assess_entry(
        request(current_instrument_exposure=Decimal("20000")),
        snapshot(),
    )

    assert assessment.decision is RiskDecision.REJECT
    assert assessment.reason_code == PortfolioRiskReason.MAX_POSITION_NOTIONAL
    assert assessment.approved_quantity == 0


def test_portfolio_risk_rejects_resulting_gross_exposure_over_limit():
    assessment = PortfolioRiskEngine(limits()).assess_entry(
        request(),
        snapshot(gross_exposure=Decimal("95000")),
    )

    assert assessment.decision is RiskDecision.REJECT
    assert assessment.reason_code == PortfolioRiskReason.MAX_GROSS_EXPOSURE


def test_portfolio_risk_rejects_new_position_when_count_limit_is_reached():
    assessment = PortfolioRiskEngine(limits()).assess_entry(
        request(opens_new_position=True),
        snapshot(open_positions=5),
    )

    assert assessment.decision is RiskDecision.REJECT
    assert assessment.reason_code == PortfolioRiskReason.MAX_OPEN_POSITIONS


def test_portfolio_risk_allows_addition_at_position_count_limit():
    assessment = PortfolioRiskEngine(limits()).assess_entry(
        request(opens_new_position=False),
        snapshot(open_positions=5),
    )

    assert assessment.decision is RiskDecision.APPROVE


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_portfolio_risk_limits_reject_non_finite_numerics(value):
    with pytest.raises(ValidationError):
        PortfolioRiskLimits(
            max_position_notional=Decimal(value),
            max_gross_exposure=Decimal("100000"),
            max_open_positions=5,
        )


@pytest.mark.parametrize("field", [
    "proposed_quantity",
    "reference_price",
    "current_instrument_exposure",
])
def test_portfolio_risk_request_rejects_non_finite_numerics(field):
    with pytest.raises(ValidationError):
        request(**{field: Decimal("Infinity")})


def test_portfolio_risk_snapshot_rejects_non_finite_gross_exposure():
    with pytest.raises(ValidationError):
        snapshot(gross_exposure=Decimal("Infinity"))
