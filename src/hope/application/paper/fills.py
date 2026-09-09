from __future__ import annotations

from datetime import timezone
from hashlib import sha256
from typing import Protocol

from hope.application.paper.context import PaperCycleContext
from hope.application.paper.effects import PaperEffect, PaperEffectType, create_paper_effect
from hope.domain.execution.simulator import Fill


def paper_fill_payload_hash(fill: Fill) -> str:
    if not fill.quantity.is_finite() or fill.quantity <= 0:
        raise ValueError("PAPER_FILL_QUANTITY_INVALID")
    if not fill.price.is_finite() or fill.price <= 0:
        raise ValueError("PAPER_FILL_PRICE_INVALID")
    if not fill.commission.is_finite() or fill.commission < 0:
        raise ValueError("PAPER_FILL_COMMISSION_INVALID")
    if not fill.slippage.is_finite() or fill.slippage < 0:
        raise ValueError("PAPER_FILL_SLIPPAGE_INVALID")
    if not fill.cost_model_version.strip():
        raise ValueError("PAPER_FILL_COST_MODEL_VERSION_REQUIRED")
    if fill.fill_time is None or fill.fill_time.tzinfo is None or fill.fill_time.utcoffset() is None:
        raise ValueError("PAPER_FILL_TIME_MUST_BE_TIMEZONE_AWARE")
    canonical = "|".join((str(fill.fill_id), str(fill.order_id), str(fill.signal_id), str(fill.instrument_id), fill.side.value,
        format(fill.quantity.normalize(), "f"), format(fill.price.normalize(), "f"), format(fill.commission.normalize(), "f"),
        format(fill.slippage.normalize(), "f"), fill.cost_model_version.strip(), fill.fill_time.astimezone(timezone.utc).isoformat()))
    return sha256(canonical.encode("utf-8")).hexdigest()


class PaperFillPersistence(Protocol):
    def persist(self, effect: PaperEffect, fill: Fill) -> bool: ...


class PaperFillWriter:
    def __init__(self, repository: PaperFillPersistence) -> None:
        self._repository = repository

    def record(self, context: PaperCycleContext, fill: Fill, *, sequence: int) -> bool:
        expected_fill_id = context.fill_id(fill.signal_id, sequence)
        if fill.fill_id != expected_fill_id:
            raise ValueError("PAPER_FILL_IDENTITY_MISMATCH")
        effect = create_paper_effect(context.job_run, PaperEffectType.FILL, fill.fill_id, paper_fill_payload_hash(fill))
        return self._repository.persist(effect, fill)
