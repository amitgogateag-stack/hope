from decimal import Decimal
from uuid import UUID

import pytest

from hope.domain.portfolio.ledger import PortfolioState, PositionState
from hope.domain.risk.performance import derive_portfolio_risk_performance_state


LONG = UUID("11111111-1111-1111-1111-111111111111")
SHORT = UUID("22222222-2222-2222-2222-222222222222")
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


def test_performance_state_derives_equity_daily_loss_and_drawdown():
    state = PortfolioState(
        cash=Decimal("1000"),
        positions={
            LONG: position(LONG, "2"),
            SHORT: position(SHORT, "-3"),
        },
    )

    result = derive_portfolio_risk_performance_state(
        state,
        {LONG: Decimal("110"), SHORT: Decimal("50")},
        session_start_equity=Decimal("1200"),
        peak_equity=Decimal("1300"),
    )

    assert result.current_equity == Decimal("1070")
    assert result.current_daily_loss == Decimal("130")
    assert result.current_drawdown == Decimal("230")


def test_performance_state_daily_loss_floors_at_zero_after_session_gain():
    state = PortfolioState(cash=Decimal("1250"), positions={})

    result = derive_portfolio_risk_performance_state(
        state,
        {},
        session_start_equity=Decimal("1200"),
        peak_equity=Decimal("1250"),
    )

    assert result.current_daily_loss == 0
    assert result.current_drawdown == 0


def test_performance_state_closed_history_does_not_require_mark():
    state = PortfolioState(
        cash=Decimal("1000"),
        positions={CLOSED: position(CLOSED, "0")},
    )

    result = derive_portfolio_risk_performance_state(
        state,
        {},
        session_start_equity=Decimal("1000"),
        peak_equity=Decimal("1000"),
    )

    assert result.current_equity == Decimal("1000")


def test_performance_state_requires_mark_for_every_open_position():
    state = PortfolioState(
        cash=Decimal("1000"),
        positions={LONG: position(LONG, "1")},
    )

    with pytest.raises(KeyError, match="MISSING_MARK"):
        derive_portfolio_risk_performance_state(
            state,
            {},
            session_start_equity=Decimal("1100"),
            peak_equity=Decimal("1100"),
        )


@pytest.mark.parametrize("mark", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_performance_state_rejects_nonfinite_marks(mark):
    state = PortfolioState(
        cash=Decimal("1000"),
        positions={LONG: position(LONG, "1")},
    )

    with pytest.raises(ValueError, match="MARK_PRICE_MUST_BE_FINITE"):
        derive_portfolio_risk_performance_state(
            state,
            {LONG: mark},
            session_start_equity=Decimal("1100"),
            peak_equity=Decimal("1100"),
        )


@pytest.mark.parametrize("mark", [Decimal("0"), Decimal("-1")])
def test_performance_state_rejects_nonpositive_marks(mark):
    state = PortfolioState(
        cash=Decimal("1000"),
        positions={LONG: position(LONG, "1")},
    )

    with pytest.raises(ValueError, match="MARK_PRICE_MUST_BE_POSITIVE"):
        derive_portfolio_risk_performance_state(
            state,
            {LONG: mark},
            session_start_equity=Decimal("1100"),
            peak_equity=Decimal("1100"),
        )


@pytest.mark.parametrize("anchor", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_performance_state_rejects_nonfinite_equity_anchors(anchor):
    state = PortfolioState(cash=Decimal("1000"), positions={})

    with pytest.raises(ValueError, match="PORTFOLIO_RISK_EQUITY_ANCHOR_MUST_BE_FINITE"):
        derive_portfolio_risk_performance_state(
            state,
            {},
            session_start_equity=anchor,
            peak_equity=Decimal("1000"),
        )


def test_performance_state_rejects_peak_below_current_equity():
    state = PortfolioState(cash=Decimal("1000"), positions={})

    with pytest.raises(ValueError, match="PORTFOLIO_RISK_PEAK_EQUITY_INCONSISTENT"):
        derive_portfolio_risk_performance_state(
            state,
            {},
            session_start_equity=Decimal("900"),
            peak_equity=Decimal("999"),
        )


def test_performance_state_rejects_peak_below_session_start_equity():
    state = PortfolioState(cash=Decimal("900"), positions={})

    with pytest.raises(ValueError, match="PORTFOLIO_RISK_PEAK_EQUITY_INCONSISTENT"):
        derive_portfolio_risk_performance_state(
            state,
            {},
            session_start_equity=Decimal("1000"),
            peak_equity=Decimal("999"),
        )
