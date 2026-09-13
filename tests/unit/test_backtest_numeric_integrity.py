from decimal import Decimal

import pytest

from hope.application.backtests.engine import DeterministicBacktest
from hope.domain.execution.simulator import CostModel


@pytest.mark.parametrize("max_fill_quantity", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_backtest_rejects_non_finite_max_fill_quantity(max_fill_quantity: Decimal):
    with pytest.raises(ValueError, match="MAX_FILL_QUANTITY_MUST_BE_FINITE"):
        DeterministicBacktest(
            Decimal("10000"),
            CostModel(version="test"),
            max_fill_quantity=max_fill_quantity,
        )
