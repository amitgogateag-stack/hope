from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from hope.application.backtests.analytics import BacktestMetrics
from hope.application.backtests.engine import BacktestResult
from hope.application.backtests.evidence import (
    BACKTEST_EVIDENCE_RESULT_FIELDS,
    BACKTEST_EVIDENCE_SCHEMA,
    project_backtest_result,
)
from hope.application.experiments.research_runs import research_result_fingerprint
from hope.domain.portfolio.ledger import PortfolioState, PositionState
from hope.domain.portfolio.valuation import PortfolioValuation


def _result(*, cash: Decimal = Decimal("100.00")) -> BacktestResult:
    first = UUID("00000000-0000-0000-0000-000000000001")
    second = UUID("00000000-0000-0000-0000-000000000002")
    positions = {
        second: PositionState(second, Decimal("2.00"), Decimal("10.500"), Decimal("1.0"), Decimal("0.20")),
        first: PositionState(first, Decimal("1"), Decimal("20"), Decimal("0"), Decimal("0.10")),
    }
    state = PortfolioState(cash=cash, positions=positions)
    at = datetime(2026, 1, 2, 15, 30, tzinfo=timezone.utc)
    valuation = PortfolioValuation(
        at,
        cash,
        Decimal("41"),
        Decimal("1"),
        Decimal("0"),
        Decimal("0.30"),
        cash + Decimal("41"),
        Decimal("41"),
    )
    metrics = BacktestMetrics(
        Decimal("100"),
        cash + Decimal("41"),
        Decimal("41"),
        Decimal("0.41"),
        Decimal("0.1"),
        Decimal("0.2"),
        Decimal("1.5"),
        Decimal("0.1"),
        Decimal("2"),
    )
    return BacktestResult(Decimal("100.0"), state, (), (valuation,), metrics, (second, first), ())


def test_backtest_evidence_v1_has_explicit_stable_root_contract() -> None:
    projected = project_backtest_result(_result())
    assert projected["schema"] == "hope.backtest-result.v1" == BACKTEST_EVIDENCE_SCHEMA
    assert tuple(projected["result"]) == BACKTEST_EVIDENCE_RESULT_FIELDS
    assert BACKTEST_EVIDENCE_RESULT_FIELDS == (
        "initial_cash",
        "final_state",
        "events",
        "valuations",
        "metrics",
        "unfilled_order_ids",
        "decisions",
    )


def test_backtest_evidence_is_versioned_json_and_canonicalizes_domain_scalars() -> None:
    projected = project_backtest_result(_result())
    result = projected["result"]
    assert result["initial_cash"] == "100"
    assert result["final_state"]["cash"] == "100"
    assert list(result["final_state"]["positions"]) == [
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
    ]
    assert result["final_state"]["positions"]["00000000-0000-0000-0000-000000000002"] == {
        "instrument_id": "00000000-0000-0000-0000-000000000002",
        "quantity": "2",
        "average_price": "10.5",
        "realized_pnl": "1",
        "total_commission": "0.2",
    }
    assert result["valuations"][0]["as_of"] == "2026-01-02T15:30:00+00:00"
    assert result["metrics"]["sharpe"] == "1.5"
    assert result["unfilled_order_ids"] == [
        "00000000-0000-0000-0000-000000000002",
        "00000000-0000-0000-0000-000000000001",
    ]
    assert result["events"] == []
    assert result["decisions"] == []


def test_backtest_result_fingerprint_is_stable_across_decimal_scale_and_mapping_order() -> None:
    left = _result(cash=Decimal("100.00"))
    right = _result(cash=Decimal("100.0000"))
    canonical_left, fingerprint_left = research_result_fingerprint(left)
    canonical_right, fingerprint_right = research_result_fingerprint(right)
    assert canonical_left == canonical_right
    assert fingerprint_left == fingerprint_right
    assert len(fingerprint_left) == 64


def test_backtest_result_fingerprint_changes_when_economic_result_changes() -> None:
    _, first = research_result_fingerprint(_result(cash=Decimal("100")))
    _, changed = research_result_fingerprint(_result(cash=Decimal("99")))
    assert first != changed


def test_backtest_evidence_rejects_nonfinite_decimal() -> None:
    with pytest.raises(ValueError, match="DECIMAL_MUST_BE_FINITE"):
        project_backtest_result(_result(cash=Decimal("NaN")))
