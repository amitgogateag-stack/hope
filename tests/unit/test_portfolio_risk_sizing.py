from decimal import Decimal

import pytest
from pydantic import ValidationError

from hope.domain.risk.sizing import PositionSizingRequest, size_position


def request(**overrides) -> PositionSizingRequest:
    values = {
        "risk_budget_cash": Decimal("1000"),
        "entry_price": Decimal("100"),
        "stop_price": Decimal("95"),
        "quantity_step": Decimal("1"),
    }
    values.update(overrides)
    return PositionSizingRequest(**values)


def test_position_sizing_uses_absolute_stop_distance_for_long_side():
    result = size_position(request())

    assert result.risk_per_unit == Decimal("5")
    assert result.quantity == Decimal("200")
    assert result.planned_cash_risk == Decimal("1000")
    assert result.unused_cash_risk_budget == Decimal("0")


def test_position_sizing_uses_absolute_stop_distance_for_short_side():
    result = size_position(request(entry_price=Decimal("100"), stop_price=Decimal("105")))

    assert result.risk_per_unit == Decimal("5")
    assert result.quantity == Decimal("200")
    assert result.planned_cash_risk == Decimal("1000")


def test_position_sizing_floors_to_explicit_quantity_step_without_exceeding_budget():
    result = size_position(
        request(
            risk_budget_cash=Decimal("100"),
            entry_price=Decimal("50"),
            stop_price=Decimal("47"),
            quantity_step=Decimal("0.25"),
        )
    )

    assert result.quantity == Decimal("33.25")
    assert result.planned_cash_risk == Decimal("99.75")
    assert result.unused_cash_risk_budget == Decimal("0.25")


def test_position_sizing_can_return_zero_when_one_step_exceeds_budget():
    result = size_position(
        request(
            risk_budget_cash=Decimal("2"),
            entry_price=Decimal("100"),
            stop_price=Decimal("95"),
            quantity_step=Decimal("1"),
        )
    )

    assert result.quantity == Decimal("0")
    assert result.planned_cash_risk == Decimal("0")
    assert result.unused_cash_risk_budget == Decimal("2")


def test_position_sizing_preserves_decimal_step_exactly():
    result = size_position(
        request(
            risk_budget_cash=Decimal("10"),
            entry_price=Decimal("20"),
            stop_price=Decimal("19.6"),
            quantity_step=Decimal("0.1"),
        )
    )

    assert result.quantity == Decimal("25.0")
    assert result.planned_cash_risk == Decimal("10.00")


def test_position_sizing_requires_request_type():
    with pytest.raises(TypeError, match="POSITION_SIZING_REQUEST_REQUIRED"):
        size_position(object())


@pytest.mark.parametrize(
    "field",
    ["risk_budget_cash", "entry_price", "stop_price", "quantity_step"],
)
@pytest.mark.parametrize(
    "value",
    [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")],
)
def test_position_sizing_rejects_nonfinite_inputs(field: str, value: Decimal):
    with pytest.raises(ValidationError):
        request(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("risk_budget_cash", Decimal("0")),
        ("entry_price", Decimal("0")),
        ("stop_price", Decimal("0")),
        ("quantity_step", Decimal("0")),
        ("risk_budget_cash", Decimal("-1")),
        ("entry_price", Decimal("-1")),
        ("stop_price", Decimal("-1")),
        ("quantity_step", Decimal("-1")),
    ],
)
def test_position_sizing_requires_positive_inputs(field: str, value: Decimal):
    with pytest.raises(ValidationError):
        request(**{field: value})


def test_position_sizing_rejects_zero_stop_distance():
    with pytest.raises(ValidationError, match="POSITION_SIZING_STOP_DISTANCE_MUST_BE_POSITIVE"):
        request(entry_price=Decimal("100"), stop_price=Decimal("100"))
