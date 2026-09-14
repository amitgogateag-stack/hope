from decimal import Decimal
from uuid import uuid4

import pytest

from hope.domain.execution.models import Environment, Order, OrderSide


def make_order(quantity: Decimal) -> Order:
    return Order(
        order_id=uuid4(),
        signal_id=uuid4(),
        instrument_id=uuid4(),
        side=OrderSide.BUY,
        quantity=quantity,
        environment=Environment.PAPER,
    )


@pytest.mark.parametrize("quantity", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_order_quantity_must_be_finite(quantity) -> None:
    with pytest.raises(ValueError, match="ORDER_QUANTITY_MUST_BE_FINITE"):
        make_order(quantity)


@pytest.mark.parametrize("quantity", [Decimal("0"), Decimal("-1")])
def test_order_quantity_must_be_positive(quantity) -> None:
    with pytest.raises(ValueError, match="ORDER_QUANTITY_MUST_BE_POSITIVE"):
        make_order(quantity)


def test_order_accepts_positive_finite_quantity() -> None:
    assert make_order(Decimal("1")).quantity == Decimal("1")
