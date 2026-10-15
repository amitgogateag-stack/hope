from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from re import fullmatch
from typing import Protocol
from uuid import UUID

from hope.application.paper.context import PaperCycleContext
from hope.application.paper.effects import PaperEffect, PaperEffectType, create_paper_effect


@dataclass(frozen=True)
class PaperNoTradeDecision:
    """Durable proof that a scheduled strategy decision produced no signals."""

    decision_id: UUID
    strategy_version: str
    decision_time: datetime
    inputs_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.decision_id, UUID):
            raise ValueError("PAPER_NO_TRADE_DECISION_ID_INVALID")
        if (
            not isinstance(self.strategy_version, str)
            or not self.strategy_version
            or self.strategy_version != self.strategy_version.strip()
        ):
            raise ValueError("PAPER_NO_TRADE_STRATEGY_VERSION_INVALID")
        if (
            not isinstance(self.decision_time, datetime)
            or self.decision_time.tzinfo is None
            or self.decision_time.utcoffset() is None
        ):
            raise ValueError("PAPER_NO_TRADE_DECISION_TIME_MUST_BE_TIMEZONE_AWARE")
        if not isinstance(self.inputs_hash, str) or fullmatch(r"[0-9a-f]{64}", self.inputs_hash) is None:
            raise ValueError("PAPER_NO_TRADE_INPUTS_HASH_INVALID")


def paper_no_trade_payload_hash(decision: PaperNoTradeDecision) -> str:
    """Hash the full canonical no-trade identity and its strategy inputs."""
    decision.__post_init__()
    canonical = "|".join(
        (
            str(decision.decision_id),
            decision.strategy_version,
            decision.decision_time.astimezone(timezone.utc).isoformat(),
            decision.inputs_hash,
        )
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


class PaperNoTradePersistence(Protocol):
    def persist(self, effect: PaperEffect, decision: PaperNoTradeDecision) -> bool:
        ...


class PaperNoTradeWriter:
    """Persist an explicit zero-signal outcome behind the PAPER effect ledger."""

    def __init__(self, repository: PaperNoTradePersistence) -> None:
        self._repository = repository

    def record(
        self,
        context: PaperCycleContext,
        *,
        strategy_version: str,
        decision_time: datetime,
        inputs_hash: str,
    ) -> bool:
        if decision_time.astimezone(timezone.utc) != context.job_run.scheduled_for:
            raise ValueError("PAPER_NO_TRADE_DECISION_TIME_MISMATCH")
        decision = PaperNoTradeDecision(
            decision_id=context.job_run.job_run_id,
            strategy_version=strategy_version,
            decision_time=decision_time,
            inputs_hash=inputs_hash,
        )
        effect = create_paper_effect(
            context.job_run,
            PaperEffectType.NO_TRADE,
            decision.decision_id,
            paper_no_trade_payload_hash(decision),
        )
        return self._repository.persist(effect, decision)
