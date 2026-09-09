from __future__ import annotations

from datetime import timezone
from hashlib import sha256
from typing import Protocol

from hope.application.paper.context import PaperCycleContext
from hope.application.paper.effects import PaperEffect, PaperEffectType, create_paper_effect
from hope.domain.signal.models import Signal


def paper_signal_payload_hash(signal: Signal) -> str:
    """Hash the canonical material content of one PAPER signal."""
    if signal.decision_time.tzinfo is None or signal.decision_time.utcoffset() is None:
        raise ValueError("PAPER_SIGNAL_DECISION_TIME_MUST_BE_TIMEZONE_AWARE")

    canonical_strategy = signal.strategy_version.strip()
    if not canonical_strategy:
        raise ValueError("PAPER_SIGNAL_STRATEGY_VERSION_REQUIRED")
    if not signal.conviction.is_finite() or signal.conviction < 0 or signal.conviction > 1:
        raise ValueError("PAPER_SIGNAL_CONVICTION_INVALID")

    canonical = "|".join(
        (
            str(signal.signal_id),
            str(signal.instrument_id),
            canonical_strategy,
            signal.decision_time.astimezone(timezone.utc).isoformat(),
            signal.signal_type.value,
            format(signal.conviction.normalize(), "f"),
            signal.inputs_hash,
        )
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


class PaperSignalPersistence(Protocol):
    def persist(self, effect: PaperEffect, signal: Signal) -> bool:
        ...


class PaperSignalWriter:
    """Validate deterministic PAPER signal identity before durable persistence."""

    def __init__(self, repository: PaperSignalPersistence) -> None:
        self._repository = repository

    def record(self, context: PaperCycleContext, signal: Signal) -> bool:
        expected_signal_id = context.signal_id(
            instrument_id=signal.instrument_id,
            strategy_version=signal.strategy_version,
            decision_time=signal.decision_time,
            signal_type=signal.signal_type,
            conviction=signal.conviction,
            inputs_hash=signal.inputs_hash,
        )
        if signal.signal_id != expected_signal_id:
            raise ValueError("PAPER_SIGNAL_IDENTITY_MISMATCH")

        effect = create_paper_effect(
            context.job_run,
            PaperEffectType.SIGNAL,
            signal.signal_id,
            paper_signal_payload_hash(signal),
        )
        return self._repository.persist(effect, signal)
