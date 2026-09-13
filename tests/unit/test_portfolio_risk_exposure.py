from decimal import Decimal
from uuid import UUID

import pytest

from hope.domain.portfolio.ledger import PortfolioState, PositionState
from hope.domain.risk.exposure import derive_portfolio_exposure_state


TARGET = UUID("11111111-1111-1111-1111-111111111111")
OTHER = UUID("22222222-2222-2222-2222-222222222222")
CLOSED = UUID("33333333-3333-3333-3333-333333333333")


def position(instrument_id: UUID, quantity: str, average_price: str = "100") -> PositionState:
    qty = Decimal(quantity)
    return PositionState(
        instrument_id=instrument_id,
        quantity=qty,
        average_price=Decimal("0") if qty == 0 else Decimal(average_price),
        realized_pnl=Decimal("0"),
        total_commission=Decimal("0"),
    )


def test_derive_exposure_state_marks_long_and_short_positions_absolutely():
    state = PortfolioState(
        cash=Decimal("1000"),
        positions={
            TARGET: position(TARGET, "2"),
            OTHER: position(OTHER, "-3"),
        },
    )

    derived = derive_portfolio_exposure_state(
        state,
        {TARGET: Decimal("110"), OTHER: Decimal("50")},
        TARGET,
    )

    assert derived.gross_exposure == Decimal("370")
    assert derived.open_positions == 2
    assert derived.current_instrument_exposure == Decimal("220")
    assert derived.opens_new_position is False


def test_derive_exposure_state_uses_absolute_target_exposure_for_short_position():
    state = PortfolioState(
        cash=Decimal("1000"),
        positions={TARGET: position(TARGET, "-4")},
    )

    derived = derive_portfolio_exposure_state(
        state,
        {TARGET: Decimal("25")},
        TARGET,
    )

    assert derived.current_instrument_exposure == Decimal("100")
    assert derived.opens_new_position is False


def test_derive_exposure_state_treats_closed_history_as_flat_without_mark():
    state = PortfolioState(
        cash=Decimal("1000"),
        positions={CLOSED: position(CLOSED, "0")},
    )

    derived = derive_portfolio_exposure_state(state, {}, CLOSED)

    assert derived.gross_exposure == 0
    assert derived.open_positions == 0
    assert derived.current_instrument_exposure == 0
    assert derived.opens_new_position is True


def test_derive_exposure_state_reports_new_target_when_other_positions_are_open():
    state = PortfolioState(
        cash=Decimal("1000"),
        positions={OTHER: position(OTHER, "5")},
    )

    derived = derive_portfolio_exposure_state(
        state,
        {OTHER: Decimal("20")},
        TARGET,
    )

    assert derived.gross_exposure == Decimal("100")
    assert derived.open_positions == 1
    assert derived.current_instrument_exposure == 0
    assert derived.opens_new_position is True


def test_derive_exposure_state_requires_mark_for_every_open_position():
    state = PortfolioState(
        cash=Decimal("1000"),
        positions={OTHER: position(OTHER, "1")},
    )

    with pytest.raises(KeyError, match="MISSING_MARK"):
        derive_portfolio_exposure_state(state, {}, TARGET)


@pytest.mark.parametrize("mark", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_derive_exposure_state_rejects_non_finite_marks(mark: Decimal):
    state = PortfolioState(
        cash=Decimal("1000"),
        positions={OTHER: position(OTHER, "1")},
    )

    with pytest.raises(ValueError, match="MARK_PRICE_MUST_BE_FINITE"):
        derive_portfolio_exposure_state(state, {OTHER: mark}, TARGET)


@pytest.mark.parametrize("mark", [Decimal("0"), Decimal("-1")])
def test_derive_exposure_state_rejects_non_positive_marks(mark: Decimal):
    state = PortfolioState(
        cash=Decimal("1000"),
        positions={OTHER: position(OTHER, "1")},
    )

    with pytest.raises(ValueError, match="MARK_PRICE_MUST_BE_POSITIVE"):
        derive_portfolio_exposure_state(state, {OTHER: mark}, TARGET)


def test_derive_exposure_state_reuses_portfolio_state_integrity_validation():
    state = PortfolioState(
        cash=Decimal("1000"),
        positions={OTHER: position(TARGET, "1")},
    )

    with pytest.raises(ValueError, match="PORTFOLIO_POSITION_KEY_MISMATCH"):
        derive_portfolio_exposure_state(state, {OTHER: Decimal("100")}, TARGET)
