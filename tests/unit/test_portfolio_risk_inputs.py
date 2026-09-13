from decimal import Decimal
from inspect import Parameter, signature
from uuid import UUID

import pytest
from pydantic import ValidationError

from hope.domain.portfolio.ledger import PortfolioState, PositionState
from hope.domain.risk.inputs import build_portfolio_entry_risk_inputs
from hope.domain.risk.portfolio import PortfolioConcentrationContext


SIGNAL_ID = UUID("11111111-1111-1111-1111-111111111111")
TARGET = UUID("22222222-2222-2222-2222-222222222222")
OTHER = UUID("33333333-3333-3333-3333-333333333333")


def position(instrument_id: UUID, quantity: str, average_price: str = "100") -> PositionState:
    qty = Decimal(quantity)
    return PositionState(
        instrument_id=instrument_id,
        quantity=qty,
        average_price=Decimal("0") if qty == 0 else Decimal(average_price),
        realized_pnl=Decimal("0"),
        total_commission=Decimal("0"),
    )


def build(**overrides):
    values = {
        "signal_id": SIGNAL_ID,
        "instrument_id": TARGET,
        "strategy_version": "strategy-v1",
        "proposed_quantity": Decimal("2"),
        "reference_price": Decimal("120"),
        "portfolio_state": PortfolioState(
            cash=Decimal("1000"),
            positions={
                TARGET: position(TARGET, "3"),
                OTHER: position(OTHER, "-4"),
            },
        ),
        "marks": {TARGET: Decimal("110"), OTHER: Decimal("50")},
        "current_strategy_exposure": Decimal("0"),
        "current_daily_loss": Decimal("0"),
        "current_drawdown": Decimal("0"),
    }
    values.update(overrides)
    return build_portfolio_entry_risk_inputs(**values)


def test_risk_input_builder_derives_portfolio_and_target_exposure():
    inputs = build()

    assert inputs.request.current_instrument_exposure == Decimal("330")
    assert inputs.request.opens_new_position is False
    assert inputs.snapshot.gross_exposure == Decimal("530")
    assert inputs.snapshot.open_positions == 2


def test_risk_input_builder_derives_new_position_when_target_is_flat():
    state = PortfolioState(
        cash=Decimal("1000"),
        positions={OTHER: position(OTHER, "2")},
    )

    inputs = build(
        portfolio_state=state,
        marks={OTHER: Decimal("75")},
    )

    assert inputs.request.current_instrument_exposure == 0
    assert inputs.request.opens_new_position is True
    assert inputs.snapshot.gross_exposure == Decimal("150")
    assert inputs.snapshot.open_positions == 1


def test_risk_input_builder_preserves_explicit_nonledger_risk_context():
    concentration = PortfolioConcentrationContext(
        group="technology",
        current_exposure=Decimal("4000"),
    )

    inputs = build(
        current_strategy_exposure=Decimal("2500"),
        concentration=concentration,
        current_daily_loss=Decimal("700"),
        current_drawdown=Decimal("1200"),
    )

    assert inputs.request.current_strategy_exposure == Decimal("2500")
    assert inputs.request.concentration == concentration
    assert inputs.snapshot.current_daily_loss == Decimal("700")
    assert inputs.snapshot.current_drawdown == Decimal("1200")


@pytest.mark.parametrize(
    "field",
    ["current_strategy_exposure", "current_daily_loss", "current_drawdown"],
)
def test_risk_input_builder_requires_nonledger_risk_context(field):
    parameter = signature(build_portfolio_entry_risk_inputs).parameters[field]
    assert parameter.default is Parameter.empty


def test_risk_input_builder_fails_closed_when_open_position_mark_is_missing():
    with pytest.raises(KeyError, match="MISSING_MARK"):
        build(marks={TARGET: Decimal("110")})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("current_strategy_exposure", Decimal("Infinity")),
        ("current_daily_loss", Decimal("Infinity")),
        ("current_drawdown", Decimal("Infinity")),
    ],
)
def test_risk_input_builder_keeps_explicit_context_validation(field, value):
    with pytest.raises(ValidationError):
        build(**{field: value})


def test_risk_input_builder_rejects_invalid_strategy_version_through_request_contract():
    with pytest.raises(ValidationError):
        build(strategy_version=" strategy-v1")
