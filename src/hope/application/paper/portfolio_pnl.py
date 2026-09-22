from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from hope.application.paper.context import PaperCycleContext
from hope.application.paper.effects import PaperEffect, PaperEffectType, create_paper_effect
from hope.domain.execution.simulator import Fill
from hope.domain.portfolio.ledger import PortfolioFillTransition


@dataclass(frozen=True)
class PaperPortfolioPnLEvent:
    pnl_event_id: UUID
    portfolio_id: UUID
    fill_id: UUID
    instrument_id: UUID
    realized_pnl_delta: Decimal
    commission_delta: Decimal
    event_time: datetime

    def __post_init__(self) -> None:
        if not self.realized_pnl_delta.is_finite():
            raise ValueError("PAPER_PORTFOLIO_PNL_REALIZED_DELTA_INVALID")
        if not self.commission_delta.is_finite() or self.commission_delta < 0:
            raise ValueError("PAPER_PORTFOLIO_PNL_COMMISSION_DELTA_INVALID")
        for value, error in (
            (self.pnl_event_id, "PAPER_PORTFOLIO_PNL_EVENT_ID_INVALID"),
            (self.portfolio_id, "PAPER_PORTFOLIO_PNL_PORTFOLIO_ID_INVALID"),
            (self.fill_id, "PAPER_PORTFOLIO_PNL_FILL_ID_INVALID"),
            (self.instrument_id, "PAPER_PORTFOLIO_PNL_INSTRUMENT_ID_INVALID"),
        ):
            if not isinstance(value, UUID):
                raise ValueError(error)
        if self.event_time.tzinfo is None or self.event_time.utcoffset() is None:
            raise ValueError("PAPER_PORTFOLIO_PNL_EVENT_TIME_MUST_BE_TIMEZONE_AWARE")
        expected_id = paper_portfolio_pnl_event_id(self.portfolio_id, self.fill_id)
        if self.pnl_event_id != expected_id:
            raise ValueError("PAPER_PORTFOLIO_PNL_IDENTITY_MISMATCH")


def paper_portfolio_pnl_event_id(portfolio_id: UUID, fill_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"hope:paper:portfolio-pnl:{portfolio_id}:{fill_id}")


def create_paper_portfolio_pnl_event(
    portfolio_id: UUID,
    fill: Fill,
    transition: PortfolioFillTransition,
) -> PaperPortfolioPnLEvent:
    if transition.fill_id != fill.fill_id:
        raise ValueError("PAPER_PORTFOLIO_PNL_FILL_MISMATCH")
    if transition.instrument_id != fill.instrument_id:
        raise ValueError("PAPER_PORTFOLIO_PNL_INSTRUMENT_MISMATCH")
    if not transition.realized_pnl_delta.is_finite():
        raise ValueError("PAPER_PORTFOLIO_PNL_REALIZED_DELTA_INVALID")
    if not transition.commission_delta.is_finite() or transition.commission_delta < 0:
        raise ValueError("PAPER_PORTFOLIO_PNL_COMMISSION_DELTA_INVALID")
    return PaperPortfolioPnLEvent(
        pnl_event_id=paper_portfolio_pnl_event_id(portfolio_id, fill.fill_id),
        portfolio_id=portfolio_id,
        fill_id=fill.fill_id,
        instrument_id=fill.instrument_id,
        realized_pnl_delta=transition.realized_pnl_delta,
        commission_delta=transition.commission_delta,
        event_time=fill.fill_time,
    )


def paper_portfolio_pnl_payload_hash(event: PaperPortfolioPnLEvent) -> str:
    canonical = "|".join(
        (
            str(event.pnl_event_id),
            str(event.portfolio_id),
            str(event.fill_id),
            str(event.instrument_id),
            format(event.realized_pnl_delta.normalize(), "f"),
            format(event.commission_delta.normalize(), "f"),
            event.event_time.astimezone(timezone.utc).isoformat(),
        )
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


class PaperPortfolioPnLPersistence(Protocol):
    def persist(self, effect: PaperEffect, event: PaperPortfolioPnLEvent) -> bool:
        ...


class PaperPortfolioPnLWriter:
    """Persist PAPER accounting only from an authoritative portfolio fill transition."""

    def __init__(self, repository: PaperPortfolioPnLPersistence) -> None:
        self._repository = repository

    def record(
        self,
        context: PaperCycleContext,
        portfolio_id: UUID,
        fill: Fill,
        transition: PortfolioFillTransition,
    ) -> bool:
        event = create_paper_portfolio_pnl_event(portfolio_id, fill, transition)
        effect = create_paper_effect(
            context.job_run,
            PaperEffectType.PNL,
            event.pnl_event_id,
            paper_portfolio_pnl_payload_hash(event),
        )
        return self._repository.persist(effect, event)
