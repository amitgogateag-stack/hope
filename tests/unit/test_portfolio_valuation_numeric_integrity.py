from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.domain.portfolio.ledger import PortfolioLedger, PortfolioState, PositionState
from hope.domain.portfolio.valuation import value_portfolio


@pytest.mark.parametrize("mark", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_portfolio_valuation_rejects_non_finite_mark(mark: Decimal) -> None:
    instrument = uuid4()
    ledger = PortfolioLedger.from_state(
        PortfolioState(
            cash=Decimal("1000"),
            positions={
                instrument: PositionState(
                    instrument_id=instrument,
                    quantity=Decimal("1"),
                    average_price=Decimal("100"),
                    realized_pnl=Decimal("0"),
                    total_commission=Decimal("0"),
                )
            },
        ),
        initial_cash=Decimal("1100"),
    )

    with pytest.raises(ValueError, match="MARK_PRICE_MUST_BE_FINITE"):
        value_portfolio(ledger, {instrument: mark}, datetime(2026, 1, 1, tzinfo=timezone.utc))
