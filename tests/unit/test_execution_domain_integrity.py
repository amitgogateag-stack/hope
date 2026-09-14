from datetime import datetime, timezone, tzinfo
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.domain.execution.models import Environment, Order, OrderSide
from hope.domain.execution.simulator import Fill


NOW = datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc)


class MissingOffsetTimezone(tzinfo):
    def utcoffset(self, dt):
        return None


def make_order(quantity: Decimal) -> Order:
    return Order(
        order_id=uuid4(),
        signal_id=uuid4(),
        instrument_id=uuid4(),
        side=OrderSide.BUY,
        quantity=quantity,
        environment=Environment.PAPER,
    )


def make_fill(**overrides) -> Fill:
    values = {
        "fill_id": uuid4(),
        "order_id": uuid4(),
        "signal_id": uuid4(),
        "instrument_id": uuid4(),
        "side": OrderSide.BUY,
        "quantity": Decimal("1"),
        "price": Decimal("100"),
        "commission": Decimal("0"),
        "slippage": Decimal("0"),
        "cost_model_version": "COST-1",
        "fill_time": NOW,
    }
    return Fill(**(values | overrides))


@pytest.mark.parametrize("quantity", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_order_quantity_must_be_finite(quantity) -> None:
    with pytest.raises(ValueError, match="ORDER_QUANTITY_MUST_BE_FINITE"):
        make_order(quantity)


@pytest.mark.parametrize("quantity", [Decimal("0"), Decimal("-1")])
def test_order_quantity_must_be_positive(quantity) -> None:
    with pytest.raises(ValueError, match="ORDER_QUANTITY_MUST_BE_POSITIVE"):
        make_order(quantity)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("quantity", Decimal("NaN"), "FILL_QUANTITY_MUST_BE_FINITE"),
        ("quantity", Decimal("Infinity"), "FILL_QUANTITY_MUST_BE_FINITE"),
        ("quantity", Decimal("0"), "FILL_QUANTITY_MUST_BE_POSITIVE"),
        ("price", Decimal("NaN"), "FILL_PRICE_MUST_BE_FINITE"),
        ("price", Decimal("Infinity"), "FILL_PRICE_MUST_BE_FINITE"),
        ("price", Decimal("0"), "FILL_PRICE_MUST_BE_POSITIVE"),
        ("commission", Decimal("Infinity"), "FILL_COSTS_MUST_BE_FINITE"),
        ("slippage", Decimal("NaN"), "FILL_COSTS_MUST_BE_FINITE"),
        ("commission", Decimal("-0.01"), "FILL_COSTS_MUST_BE_NON_NEGATIVE"),
        ("slippage", Decimal("-0.01"), "FILL_COSTS_MUST_BE_NON_NEGATIVE"),
    ],
)
def test_fill_numeric_contract_is_fail_closed(field, value, error) -> None:
    with pytest.raises(ValueError, match=error):
        make_fill(**{field: value})


@pytest.mark.parametrize(
    ("version", "error"),
    [
        ("", "FILL_COST_MODEL_VERSION_REQUIRED"),
        ("   ", "FILL_COST_MODEL_VERSION_REQUIRED"),
        (" COST-1", "FILL_COST_MODEL_VERSION_NOT_CANONICAL"),
        ("COST-1 ", "FILL_COST_MODEL_VERSION_NOT_CANONICAL"),
    ],
)
def test_fill_cost_model_version_is_canonical(version, error) -> None:
    with pytest.raises(ValueError, match=error):
        make_fill(cost_model_version=version)


@pytest.mark.parametrize(
    "fill_time",
    [
        datetime(2026, 1, 1, 14, 0),
        datetime(2026, 1, 1, 14, 0, tzinfo=MissingOffsetTimezone()),
    ],
)
def test_fill_time_must_be_unambiguously_timezone_aware(fill_time) -> None:
    with pytest.raises(ValueError, match="FILL_TIME_MUST_BE_TIMEZONE_AWARE"):
        make_fill(fill_time=fill_time)


def test_fill_accepts_valid_canonical_values() -> None:
    assert make_fill().price == Decimal("100")
