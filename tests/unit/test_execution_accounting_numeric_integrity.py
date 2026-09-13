from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.application.paper.portfolio_pnl import PaperPortfolioPnLEvent
from hope.domain.execution.models import OrderSide
from hope.domain.execution.simulator import Fill
from hope.domain.portfolio.ledger import PortfolioLedger


NOW = datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc)


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
        "cost_model_version": "test",
        "fill_time": NOW,
    }
    values.update(overrides)
    return Fill(**values)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("quantity", Decimal("NaN"), "FILL_QUANTITY_MUST_BE_FINITE"),
        ("quantity", Decimal("Infinity"), "FILL_QUANTITY_MUST_BE_FINITE"),
        ("price", Decimal("NaN"), "FILL_PRICE_MUST_BE_FINITE"),
        ("price", Decimal("Infinity"), "FILL_PRICE_MUST_BE_FINITE"),
        ("commission", Decimal("Infinity"), "FILL_COSTS_MUST_BE_FINITE"),
        ("slippage", Decimal("NaN"), "FILL_COSTS_MUST_BE_FINITE"),
    ],
)
def test_portfolio_ledger_rejects_non_finite_fill_values(field, value, error):
    ledger = PortfolioLedger(Decimal("1000"))
    with pytest.raises(ValueError, match=error):
        ledger.apply_fill(make_fill(**{field: value}))


def test_unrealized_pnl_rejects_non_finite_mark():
    ledger = PortfolioLedger(Decimal("1000"))
    fill = make_fill()
    ledger.apply_fill(fill)

    with pytest.raises(ValueError, match="MARK_PRICE_MUST_BE_FINITE"):
        ledger.unrealized_pnl({fill.instrument_id: Decimal("Infinity")})


@pytest.mark.parametrize("invalid_value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_paper_pnl_event_rejects_non_finite_realized_delta(invalid_value):
    with pytest.raises(ValueError, match="PAPER_PORTFOLIO_PNL_REALIZED_DELTA_INVALID"):
        PaperPortfolioPnLEvent(
            pnl_event_id=uuid4(),
            portfolio_id=uuid4(),
            fill_id=uuid4(),
            instrument_id=uuid4(),
            realized_pnl_delta=invalid_value,
            commission_delta=Decimal("0"),
            event_time=NOW,
        )


@pytest.mark.parametrize("invalid_value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity"), Decimal("-1")])
def test_paper_pnl_event_rejects_invalid_commission_delta(invalid_value):
    with pytest.raises(ValueError, match="PAPER_PORTFOLIO_PNL_COMMISSION_DELTA_INVALID"):
        PaperPortfolioPnLEvent(
            pnl_event_id=uuid4(),
            portfolio_id=uuid4(),
            fill_id=uuid4(),
            instrument_id=uuid4(),
            realized_pnl_delta=Decimal("0"),
            commission_delta=invalid_value,
            event_time=NOW,
        )
