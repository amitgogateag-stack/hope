from __future__ import annotations

import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Protocol
from uuid import UUID

from hope.application.paper.context import PaperCycleContext
from hope.application.paper.effects import PaperEffect, PaperEffectType, create_paper_effect


@dataclass(frozen=True)
class PaperPnLEvent:
    pnl_event_id: UUID
    position_id: UUID
    amount: Decimal
    event_time: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.pnl_event_id, UUID):
            raise ValueError("PAPER_PNL_EVENT_ID_INVALID")
        if not isinstance(self.position_id, UUID):
            raise ValueError("PAPER_PNL_POSITION_ID_INVALID")
        if not self.amount.is_finite():
            raise ValueError("PAPER_PNL_AMOUNT_MUST_BE_FINITE")
        if self.event_time.tzinfo is None or self.event_time.utcoffset() is None:
            raise ValueError("PAPER_PNL_EVENT_TIME_MUST_BE_TIMEZONE_AWARE")


def paper_pnl_payload_hash(event: PaperPnLEvent) -> str:
    canonical = "|".join(
        (
            str(event.pnl_event_id),
            str(event.position_id),
            format(event.amount.normalize(), "f"),
            event.event_time.astimezone(timezone.utc).isoformat(),
        )
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


class PaperPnLPersistence(Protocol):
    def persist(self, effect: PaperEffect, event: PaperPnLEvent) -> bool:
        ...


class PaperPnLWriter:
    """Legacy position-scoped PAPER P&L writer retained for historical compatibility.

    New PAPER runtime accounting must use PaperAccountingWriter so realized P&L and
    commission are derived from the authoritative portfolio fill transition.
    """

    def __init__(self, repository: PaperPnLPersistence) -> None:
        warnings.warn(
            "PaperPnLWriter is legacy; new PAPER runtime accounting must use PaperAccountingWriter",
            DeprecationWarning,
            stacklevel=2,
        )
        self._repository = repository

    def record(self, context: PaperCycleContext, event: PaperPnLEvent, *, sequence: int) -> bool:
        expected_id = context.pnl_event_id(event.position_id, sequence)
        if event.pnl_event_id != expected_id:
            raise ValueError("PAPER_PNL_IDENTITY_MISMATCH")

        effect = create_paper_effect(
            context.job_run,
            PaperEffectType.PNL,
            event.pnl_event_id,
            paper_pnl_payload_hash(event),
        )
        return self._repository.persist(effect, event)
