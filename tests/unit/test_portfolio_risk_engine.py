from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from hope.domain.risk.models import RiskDecision
from hope.domain.risk.portfolio import (
    PortfolioConcentrationContext,
    PortfolioEntryRiskRequest,
    PortfolioRiskEngine,
    PortfolioRiskLimits,
    PortfolioRiskReason,
    PortfolioRiskSnapshot,
)


SIGNAL_ID = UUID("11111111-1111-1111-1111-111111111111")
INSTRUMENT_ID = UUID("22222222-2222-2222-2222-222222222222")


def limits(**overrides) -> PortfolioRiskLimits:
    values = {
        "max_position_notional": Decimal("25000"),
        "max_gross_exposure": Decimal("100000"),
        "max_open_positions": 5,
    }
    values.update(overrides)
    return PortfolioRiskLimits(**values)


def request(**overrides) -> PortfolioEntryRiskRequest:
    values = {
        "signal_id": SIGNAL_ID,
        "instrument_id": INSTRUMENT_ID,
        "strategy_version": "strategy-v1",
        "proposed_quantity": Decimal("10"),
        "reference_price": Decimal("1000"),
        "current_instrument_exposure": Decimal("0"),
        "current_strategy_exposure": Decimal("0"),
        "opens_new_position": True,
    }
    values.update(overrides)
    return PortfolioEntryRiskRequest(**values)


def snapshot(**overrides) -> PortfolioRiskSnapshot:
    values = {"gross_exposure": Decimal("50000"), "open_positions": 2}
    values.update(overrides)
    return PortfolioRiskSnapshot(**values)


def concentration(**overrides) -> PortfolioConcentrationContext:
    values = {"group": "sector:technology", "current_exposure": Decimal("20000")}
    values.update(overrides)
    return PortfolioConcentrationContext(**values)


def test_portfolio_risk_approves_request_within_all_limits():
    assessment = PortfolioRiskEngine(limits()).assess_entry(request(), snapshot())

    assert assessment.decision is RiskDecision.APPROVE
    assert assessment.reason_code == PortfolioRiskReason.APPROVED
    assert assessment.approved_quantity == Decimal("10")


def test_portfolio_risk_rejects_when_daily_loss_limit_is_reached():
    assessment = PortfolioRiskEngine(
        limits(max_daily_loss=Decimal("5000"))
    ).assess_entry(
        request(),
        snapshot(current_daily_loss=Decimal("5000")),
    )

    assert assessment.decision is RiskDecision.REJECT
    assert assessment.reason_code == PortfolioRiskReason.MAX_DAILY_LOSS
    assert assessment.approved_quantity == 0


def test_portfolio_risk_rejects_when_drawdown_limit_is_reached():
    assessment = PortfolioRiskEngine(
        limits(max_drawdown=Decimal("12000"))
    ).assess_entry(
        request(),
        snapshot(current_drawdown=Decimal("12000")),
    )

    assert assessment.decision is RiskDecision.REJECT
    assert assessment.reason_code == PortfolioRiskReason.MAX_DRAWDOWN
    assert assessment.approved_quantity == 0


def test_portfolio_risk_optional_loss_gates_do_not_change_existing_policy():
    assessment = PortfolioRiskEngine(limits()).assess_entry(
        request(),
        snapshot(
            current_daily_loss=Decimal("999999"),
            current_drawdown=Decimal("999999"),
        ),
    )

    assert assessment.decision is RiskDecision.APPROVE


def test_portfolio_risk_rejects_resulting_position_notional_over_limit():
    assessment = PortfolioRiskEngine(limits()).assess_entry(
        request(current_instrument_exposure=Decimal("20000")),
        snapshot(),
    )

    assert assessment.decision is RiskDecision.REJECT
    assert assessment.reason_code == PortfolioRiskReason.MAX_POSITION_NOTIONAL
    assert assessment.approved_quantity == 0


def test_portfolio_risk_rejects_resulting_strategy_exposure_over_limit():
    assessment = PortfolioRiskEngine(
        limits(max_strategy_exposure=Decimal("40000"))
    ).assess_entry(
        request(current_strategy_exposure=Decimal("35000")),
        snapshot(),
    )

    assert assessment.decision is RiskDecision.REJECT
    assert assessment.reason_code == PortfolioRiskReason.MAX_STRATEGY_EXPOSURE
    assert assessment.approved_quantity == 0


def test_portfolio_risk_strategy_budget_is_optional():
    assessment = PortfolioRiskEngine(limits()).assess_entry(
        request(current_strategy_exposure=Decimal("999999")),
        snapshot(),
    )

    assert assessment.decision is RiskDecision.APPROVE


def test_portfolio_risk_requires_concentration_context_when_limit_is_enabled():
    assessment = PortfolioRiskEngine(
        limits(max_concentration_exposure=Decimal("50000"))
    ).assess_entry(request(), snapshot())

    assert assessment.decision is RiskDecision.REJECT
    assert assessment.reason_code == PortfolioRiskReason.CONCENTRATION_CONTEXT_REQUIRED
    assert assessment.approved_quantity == 0


def test_portfolio_risk_rejects_resulting_concentration_exposure_over_limit():
    assessment = PortfolioRiskEngine(
        limits(max_concentration_exposure=Decimal("25000"))
    ).assess_entry(
        request(concentration=concentration(current_exposure=Decimal("20000"))),
        snapshot(),
    )

    assert assessment.decision is RiskDecision.REJECT
    assert assessment.reason_code == PortfolioRiskReason.MAX_CONCENTRATION_EXPOSURE
    assert assessment.approved_quantity == 0


def test_portfolio_risk_allows_concentration_at_limit():
    assessment = PortfolioRiskEngine(
        limits(max_concentration_exposure=Decimal("30000"))
    ).assess_entry(
        request(concentration=concentration(current_exposure=Decimal("20000"))),
        snapshot(),
    )

    assert assessment.decision is RiskDecision.APPROVE


def test_portfolio_risk_concentration_control_is_optional():
    assessment = PortfolioRiskEngine(limits()).assess_entry(
        request(concentration=concentration(current_exposure=Decimal("999999"))),
        snapshot(),
    )

    assert assessment.decision is RiskDecision.APPROVE


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


@pytest.mark.parametrize("field", [
    "max_position_notional",
    "max_gross_exposure",
    "max_daily_loss",
    "max_drawdown",
    "max_strategy_exposure",
    "max_concentration_exposure",
])
def test_portfolio_risk_limits_reject_non_finite_numerics(field):
    values = {
        "max_position_notional": Decimal("25000"),
        "max_gross_exposure": Decimal("100000"),
        "max_open_positions": 5,
    }
    values[field] = Decimal("Infinity")
    with pytest.raises(ValidationError):
        PortfolioRiskLimits(**values)


@pytest.mark.parametrize("field", [
    "proposed_quantity",
    "reference_price",
    "current_instrument_exposure",
    "current_strategy_exposure",
])
def test_portfolio_risk_request_rejects_non_finite_numerics(field):
    with pytest.raises(ValidationError):
        request(**{field: Decimal("Infinity")})


@pytest.mark.parametrize("strategy_version", ["", " ", " strategy-v1", "strategy-v1 "])
def test_portfolio_risk_request_requires_canonical_strategy_version(strategy_version):
    with pytest.raises(ValidationError):
        request(strategy_version=strategy_version)


@pytest.mark.parametrize("group", ["", " ", " sector:technology", "sector:technology "])
def test_portfolio_risk_concentration_requires_canonical_group(group):
    with pytest.raises(ValidationError):
        concentration(group=group)


def test_portfolio_risk_concentration_rejects_non_finite_exposure():
    with pytest.raises(ValidationError):
        concentration(current_exposure=Decimal("Infinity"))


@pytest.mark.parametrize("field", [
    "gross_exposure",
    "current_daily_loss",
    "current_drawdown",
])
def test_portfolio_risk_snapshot_rejects_non_finite_numerics(field):
    with pytest.raises(ValidationError):
        snapshot(**{field: Decimal("Infinity")})
