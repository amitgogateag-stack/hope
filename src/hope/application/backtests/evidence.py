from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from hope.application.backtests.engine import BacktestResult


BACKTEST_EVIDENCE_SCHEMA = "hope.backtest-result.v1"


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
    """Project supported HOPE domain values into deterministic JSON primitives."""
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


def project_backtest_result(result: BacktestResult) -> dict[str, Any]:
    """Return the versioned, lossless JSON evidence contract for a backtest result."""
    return {
        "schema": BACKTEST_EVIDENCE_SCHEMA,
        "result": _canonical_value(result),
    }
