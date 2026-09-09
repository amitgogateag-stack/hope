from __future__ import annotations

from hashlib import sha256
from typing import Protocol

from hope.application.paper.context import PaperCycleContext
from hope.application.paper.effects import PaperEffect, PaperEffectType, create_paper_effect
from hope.domain.execution.models import Environment, Order


def paper_order_payload_hash(order: Order) -> str:
    """Hash the canonical material content of one PAPER order."""
    if not order.quantity.is_finite() or order.quantity <= 0:
        raise ValueError("PAPER_ORDER_QUANTITY_INVALID")

    canonical = "|".join(
        (
            str(order.order_id),
            str(order.signal_id),
            str(order.instrument_id),
            order.side.value,
            format(order.quantity.normalize(), "f"),
            order.environment.value,
            order.signal_type.value,
        )
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


class PaperOrderPersistence(Protocol):
    def persist(self, effect: PaperEffect, order: Order) -> bool:
        ...


class PaperOrderWriter:
    """Validate deterministic PAPER order identity before durable persistence."""

    def __init__(self, repository: PaperOrderPersistence) -> None:
        self._repository = repository

    def record(self, context: PaperCycleContext, order: Order) -> bool:
        if order.environment is not Environment.PAPER:
            raise ValueError("PAPER_ORDER_REQUIRES_PAPER_ENVIRONMENT")
        if not order.quantity.is_finite() or order.quantity <= 0:
            raise ValueError("PAPER_ORDER_QUANTITY_INVALID")

        expected_order_id = context.order_id(order.signal_id)
        if order.order_id != expected_order_id:
            raise ValueError("PAPER_ORDER_IDENTITY_MISMATCH")

        effect = create_paper_effect(
            context.job_run,
            PaperEffectType.ORDER,
            order.order_id,
            paper_order_payload_hash(order),
        )
        return self._repository.persist(effect, order)
