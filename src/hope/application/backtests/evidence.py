from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from hope.application.backtests.analytics import BacktestMetrics
from hope.application.backtests.engine import BacktestResult
from hope.domain.portfolio.ledger import PortfolioState, PositionState
from hope.domain.portfolio.valuation import PortfolioValuation


BACKTEST_EVIDENCE_SCHEMA = "hope.backtest-result.v1"
BACKTEST_EVIDENCE_RESULT_FIELDS = (
    "initial_cash",
    "final_state",
    "events",
    "valuations",
    "metrics",
    "unfilled_order_ids",
    "decisions",
)


def _decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("BACKTEST_EVIDENCE_DECIMAL_MUST_BE_FINITE")
    if value == 0:
        return "0"
    normalized = value.normalize()
    return format(normalized, "f")


def _datetime(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("BACKTEST_EVIDENCE_TIME_MUST_BE_TIMEZONE_AWARE")
    return value.isoformat()


def _canonical_value(value: Any) -> Any:
    """Canonicalize nested event/decision payloads not yet covered by explicit v1 projectors."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return _decimal(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _datetime(value)
    if isinstance(value, Enum):
        return _canonical_value(value.value)
    if isinstance(value, BaseModel):
        return _canonical_value(value.model_dump(mode="python"))
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _canonical_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple | list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, dict):
        projected: list[tuple[str, Any]] = []
        for key, item in value.items():
            if isinstance(key, UUID):
                canonical_key = str(key)
            elif isinstance(key, str):
                canonical_key = key
            else:
                raise TypeError("BACKTEST_EVIDENCE_UNSUPPORTED_MAPPING_KEY")
            projected.append((canonical_key, _canonical_value(item)))
        projected.sort(key=lambda pair: pair[0])
        if len({key for key, _ in projected}) != len(projected):
            raise ValueError("BACKTEST_EVIDENCE_DUPLICATE_CANONICAL_KEY")
        return {key: item for key, item in projected}
    raise TypeError(f"BACKTEST_EVIDENCE_UNSUPPORTED_TYPE:{type(value).__name__}")


def _position(position: PositionState) -> dict[str, str]:
    return {
        "instrument_id": str(position.instrument_id),
        "quantity": _decimal(position.quantity),
        "average_price": _decimal(position.average_price),
        "realized_pnl": _decimal(position.realized_pnl),
        "total_commission": _decimal(position.total_commission),
    }


def _portfolio_state(state: PortfolioState) -> dict[str, Any]:
    return {
        "cash": _decimal(state.cash),
        "positions": {
            str(instrument_id): _position(state.positions[instrument_id])
            for instrument_id in sorted(state.positions, key=str)
        },
    }


def _valuation(value: PortfolioValuation) -> dict[str, str]:
    return {
        "as_of": _datetime(value.as_of),
        "cash": _decimal(value.cash),
        "market_value": _decimal(value.market_value),
        "realized_pnl": _decimal(value.realized_pnl),
        "unrealized_pnl": _decimal(value.unrealized_pnl),
        "commissions": _decimal(value.commissions),
        "equity": _decimal(value.equity),
        "total_pnl": _decimal(value.total_pnl),
    }


def _metrics(value: BacktestMetrics) -> dict[str, str]:
    return {
        "initial_equity": _decimal(value.initial_equity),
        "final_equity": _decimal(value.final_equity),
        "total_pnl": _decimal(value.total_pnl),
        "total_return": _decimal(value.total_return),
        "max_drawdown": _decimal(value.max_drawdown),
        "volatility": _decimal(value.volatility),
        "sharpe": _decimal(value.sharpe),
        "downside_deviation": _decimal(value.downside_deviation),
        "sortino": _decimal(value.sortino),
    }


def project_backtest_result(result: BacktestResult) -> dict[str, Any]:
    """Project BacktestResult into the explicit versioned v1 research-evidence contract.

    Core portfolio/valuation/metric fields are enumerated explicitly so adding a
    new domain attribute cannot silently redefine v1. Event and decision payloads
    remain intentionally partial: their ordered containers are part of v1, while
    their nested domain objects use the deterministic canonicalizer until dedicated
    event/decision evidence schemas are introduced in a later version.
    """
    projected_result = {
        "initial_cash": _decimal(result.initial_cash),
        "final_state": _portfolio_state(result.final_state),
        "events": [_canonical_value(event) for event in result.events],
        "valuations": [_valuation(value) for value in result.valuations],
        "metrics": _metrics(result.metrics),
        "unfilled_order_ids": [str(order_id) for order_id in result.unfilled_order_ids],
        "decisions": [_canonical_value(decision) for decision in result.decisions],
    }
    if tuple(projected_result) != BACKTEST_EVIDENCE_RESULT_FIELDS:
        raise RuntimeError("BACKTEST_EVIDENCE_V1_FIELD_CONTRACT_BROKEN")
    return {
        "schema": BACKTEST_EVIDENCE_SCHEMA,
        "result": projected_result,
    }
