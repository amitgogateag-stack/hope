from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from hope.application.backtests.analytics import BacktestMetrics
from hope.application.backtests.engine import BacktestDecision, BacktestEvent, BacktestResult
from hope.application.trading.service import TradingKernelResult
from hope.domain.audit.models import AuditEvent
from hope.domain.execution.models import ExecutionCancellation, ExecutionRejection
from hope.domain.execution.simulator import Fill
from hope.domain.market_data.models import MarketBar
from hope.domain.portfolio.ledger import PortfolioState, PositionState
from hope.domain.portfolio.valuation import PortfolioValuation
from hope.domain.risk.models import RiskAssessment
from hope.domain.signal.models import Signal
from hope.domain.trading.kernel import OrderIntent


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
    return format(value.normalize(), "f")


def _datetime(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("BACKTEST_EVIDENCE_TIME_MUST_BE_TIMEZONE_AWARE")
    return value.isoformat()


def _optional_datetime(value: datetime | None) -> str | None:
    return None if value is None else _datetime(value)


def _optional_uuid(value: UUID | None) -> str | None:
    return None if value is None else str(value)


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


def _market_bar(value: MarketBar) -> dict[str, Any]:
    return {
        "instrument_id": value.instrument_id,
        "event_time": _datetime(value.event_time),
        "available_time": _datetime(value.available_time),
        "effective_time": _optional_datetime(value.effective_time),
        "ingestion_time": _datetime(value.ingestion_time),
        "open": _decimal(value.open),
        "high": _decimal(value.high),
        "low": _decimal(value.low),
        "close": _decimal(value.close),
        "volume": _decimal(value.volume),
    }


def _signal(value: Signal) -> dict[str, Any]:
    return {
        "signal_id": str(value.signal_id),
        "instrument_id": str(value.instrument_id),
        "strategy_version": value.strategy_version,
        "decision_time": _datetime(value.decision_time),
        "signal_type": value.signal_type.value,
        "conviction": _decimal(value.conviction),
        "inputs_hash": value.inputs_hash,
    }


def _risk(value: RiskAssessment) -> dict[str, str]:
    return {
        "signal_id": str(value.signal_id),
        "decision": value.decision.value,
        "reason_code": value.reason_code,
        "approved_quantity": _decimal(value.approved_quantity),
    }


def _intent(value: OrderIntent | None) -> dict[str, str] | None:
    if value is None:
        return None
    return {
        "signal_id": str(value.signal_id),
        "instrument_id": str(value.instrument_id),
        "side": value.side.value,
        "quantity": _decimal(value.quantity),
        "environment": value.environment.value,
        "signal_type": value.signal_type.value,
    }


def _fill(value: Fill | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "fill_id": str(value.fill_id),
        "order_id": str(value.order_id),
        "signal_id": str(value.signal_id),
        "instrument_id": str(value.instrument_id),
        "side": value.side.value,
        "quantity": _decimal(value.quantity),
        "price": _decimal(value.price),
        "commission": _decimal(value.commission),
        "slippage": _decimal(value.slippage),
        "cost_model_version": value.cost_model_version,
        "fill_time": _optional_datetime(value.fill_time),
    }


def _rejection(value: ExecutionRejection | None) -> dict[str, str] | None:
    if value is None:
        return None
    return {
        "order_id": str(value.order_id),
        "signal_id": str(value.signal_id),
        "instrument_id": str(value.instrument_id),
        "environment": value.environment.value,
        "reason_code": value.reason_code,
        "rejection_time": _datetime(value.rejection_time),
    }


def _cancellation(value: ExecutionCancellation | None) -> dict[str, str] | None:
    if value is None:
        return None
    return {
        "order_id": str(value.order_id),
        "signal_id": str(value.signal_id),
        "instrument_id": str(value.instrument_id),
        "environment": value.environment.value,
        "reason_code": value.reason_code,
        "cancellation_time": _datetime(value.cancellation_time),
        "cancelled_quantity": _decimal(value.cancelled_quantity),
    }


def _audit_event(value: AuditEvent) -> dict[str, Any]:
    return {
        "event_id": str(value.event_id),
        "event_type": value.event_type.value,
        "event_time": _datetime(value.event_time),
        "signal_id": _optional_uuid(value.signal_id),
        "order_id": _optional_uuid(value.order_id),
        "fill_id": _optional_uuid(value.fill_id),
        "instrument_id": _optional_uuid(value.instrument_id),
        "environment": value.environment.value,
        "payload_hash": value.payload_hash,
    }


def _kernel_result(value: TradingKernelResult) -> dict[str, Any]:
    return {
        "intent": _intent(value.intent),
        "order_id": _optional_uuid(value.order_id),
        "fill": _fill(value.fill),
        "portfolio_state": _portfolio_state(value.portfolio_state),
        "audit_events": [_audit_event(event) for event in value.audit_events],
        "rejection": _rejection(value.rejection),
        "cancellation": _cancellation(value.cancellation),
    }


def _event(value: BacktestEvent) -> dict[str, Any]:
    return {
        "event_time": _datetime(value.event_time),
        "bar": _market_bar(value.bar),
        "result": _kernel_result(value.result),
        "valuation": _valuation(value.valuation),
    }


def _decision(value: BacktestDecision) -> dict[str, Any]:
    return {
        "decision_time": _datetime(value.decision_time),
        "bar": _market_bar(value.bar),
        "signal": _signal(value.signal),
        "risk": _risk(value.risk),
        "result": _kernel_result(value.result),
        "valuation": _valuation(value.valuation),
    }


def project_backtest_result(result: BacktestResult) -> dict[str, Any]:
    """Project BacktestResult into the explicit, stable v1 research-evidence contract."""
    projected_result = {
        "initial_cash": _decimal(result.initial_cash),
        "final_state": _portfolio_state(result.final_state),
        "events": [_event(event) for event in result.events],
        "valuations": [_valuation(value) for value in result.valuations],
        "metrics": _metrics(result.metrics),
        "unfilled_order_ids": [str(order_id) for order_id in result.unfilled_order_ids],
        "decisions": [_decision(decision) for decision in result.decisions],
    }
    if tuple(projected_result) != BACKTEST_EVIDENCE_RESULT_FIELDS:
        raise RuntimeError("BACKTEST_EVIDENCE_V1_FIELD_CONTRACT_BROKEN")
    return {"schema": BACKTEST_EVIDENCE_SCHEMA, "result": projected_result}
