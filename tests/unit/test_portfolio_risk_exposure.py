from decimal import Decimal
from uuid import UUID

import pytest

from hope.domain.portfolio.ledger import PortfolioState, PositionState
from hope.domain.risk.exposure import derive_portfolio_exposure


TARGET = UUID("11111111-1111-1111-1111-111111111111")
OTHER = UUID("22222222-2222-2222-2222-222222222222")
THIRD = UUID("33333333-3333-3333-3333-333333333333")


def position(instrument_id: UUID, quantity: Decimal) -> PositionState:
    return PositionState(
        instrument_id=instrument_id,
        quantity=quantity,
        average_price=Decimal("100"),
        realized_pnl=Decimal("0"),
        total_commission=Decimal("0"),
    )


def state(*positions: PositionState) -> PortfolioState:
    return PortfolioState(
        cash=Decimal("100000"),
        positions={item.instrument_id: item for item in positions},
    )


def test_derive_portfolio_exposure_marks_long_and_short_symmetrically():
    exposure = derive_portfolio_exposure(
        state(position(TARGET, Decimal("10")), position(OTHER, Decimal("-5"))),
        {TARGET: Decimal("120"), OTHER: Decimal("80")},
        TARGET,
    )

    assert exposure.gross_exposure == Decimal("1600")
    assert exposure.current_instrument_exposure == Decimal("1200")
    assert exposure.open_positions == 2
    assert exposure.opens_new_position is False


def test_derive_portfolio_exposure_marks_absent_target_as_new_position():
    exposure = derive_portfolio_exposure(
        state(position(OTHER, Decimal("5"))),
        {OTHER: Decimal("80")},
        TARGET,
    )

    assert exposure.gross_exposure == Decimal("400")
    assert exposure.current_instrument_exposure == Decimal("0")
    assert exposure.open_positions == 1
    assert exposure.opens_new_position is True


def test_derive_portfolio_exposure_flat_target_needs_no_mark():
    flat_target = PositionState(
        instrument_id=TARGET,
        quantity=Decimal("0"),
        average_price=Decimal("0"),
        realized_pnl=Decimal("50"),
        total_commission=Decimal("2"),
    )

    exposure = derive_portfolio_exposure(
        state(flat_target, position(OTHER, Decimal("2"))),
        {OTHER: Decimal("90")},
        TARGET,
    )

    assert exposure.gross_exposure == Decimal("180")
    assert exposure.current_instrument_exposure == Decimal("0")
    assert exposure.open_positions == 1
    assert exposure.opens_new_position is True


def test_derive_portfolio_exposure_requires_marks_for_every_open_position():
    with pytest.raises(KeyError, match=f"MISSING_MARK:{OTHER}"):
        derive_portfolio_exposure(
            state(position(TARGET, Decimal("1")), position(OTHER, Decimal("1"))),
            {TARGET: Decimal("100")},
            TARGET,
        )


@pytest.mark.parametrize("mark", [Decimal("0"), Decimal("-1")])
def test_derive_portfolio_exposure_rejects_nonpositive_marks(mark):
    with pytest.raises(ValueError, match="MARK_PRICE_MUST_BE_POSITIVE"):
        derive_portfolio_exposure(
            state(position(TARGET, Decimal("1"))),
            {TARGET: mark},
            TARGET,
        )


def test_derive_portfolio_exposure_rejects_nonfinite_marks():
    with pytest.raises(ValueError, match="MARK_PRICE_MUST_BE_FINITE"):
        derive_portfolio_exposure(
            state(position(TARGET, Decimal("1"))),
            {TARGET: Decimal("Infinity")},
            TARGET,
        )


def test_derive_portfolio_exposure_rejects_nonfinite_position_quantity():
    with pytest.raises(ValueError, match="PORTFOLIO_POSITION_QUANTITY_MUST_BE_FINITE"):
        derive_portfolio_exposure(
            state(position(TARGET, Decimal("Infinity"))),
            {TARGET: Decimal("100")},
            TARGET,
        )


def test_derive_portfolio_exposure_rejects_position_key_mismatch():
    mismatched = PortfolioState(
        cash=Decimal("100000"),
        positions={TARGET: position(OTHER, Decimal("1"))},
    )

    with pytest.raises(ValueError, match="PORTFOLIO_POSITION_KEY_MISMATCH"):
        derive_portfolio_exposure(mismatched, {TARGET: Decimal("100")}, TARGET)


def test_derive_portfolio_exposure_ignores_marks_for_flat_unrelated_positions():
    flat = PositionState(
        instrument_id=THIRD,
        quantity=Decimal("0"),
        average_price=Decimal("0"),
        realized_pnl=Decimal("0"),
        total_commission=Decimal("0"),
    )

    exposure = derive_portfolio_exposure(
        state(position(TARGET, Decimal("1")), flat),
        {TARGET: Decimal("100")},
        TARGET,
    )

    assert exposure.gross_exposure == Decimal("100")
    assert exposure.open_positions == 1
