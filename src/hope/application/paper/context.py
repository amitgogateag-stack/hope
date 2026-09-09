from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from re import fullmatch
from uuid import NAMESPACE_URL, UUID, uuid5

from hope.application.jobs import ScheduledJobRun
from hope.domain.signal.models import SignalType


@dataclass(frozen=True)
class PaperCycleContext:
    """Durable PAPER-cycle provenance plus deterministic side-effect identities."""

    job_run: ScheduledJobRun

    def signal_id(
        self,
        *,
        instrument_id: UUID,
        strategy_version: str,
        decision_time: datetime,
        signal_type: SignalType,
        conviction: Decimal,
        inputs_hash: str,
    ) -> UUID:
        if decision_time.tzinfo is None or decision_time.utcoffset() is None:
            raise ValueError("PAPER_SIGNAL_DECISION_TIME_MUST_BE_TIMEZONE_AWARE")

        canonical_strategy = strategy_version.strip()
        if not canonical_strategy:
            raise ValueError("PAPER_SIGNAL_STRATEGY_VERSION_REQUIRED")

        if not conviction.is_finite() or conviction < 0 or conviction > 1:
            raise ValueError("PAPER_SIGNAL_CONVICTION_INVALID")

        if fullmatch(r"[0-9a-f]{64}", inputs_hash) is None:
            raise ValueError("PAPER_SIGNAL_INPUTS_HASH_INVALID")

        canonical_time = decision_time.astimezone(timezone.utc).isoformat()
        canonical_conviction = format(conviction.normalize(), "f")
        canonical = "|".join(
            (
                str(instrument_id),
                canonical_strategy,
                canonical_time,
                signal_type.value,
                canonical_conviction,
                inputs_hash,
            )
        )
        return uuid5(NAMESPACE_URL, f"hope:paper:signal:{canonical}")

    @staticmethod
    def order_id(signal_id: UUID) -> UUID:
        return uuid5(NAMESPACE_URL, f"hope:paper:order:{signal_id}")

    @staticmethod
    def fill_id(signal_id: UUID, sequence: int) -> UUID:
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError("PAPER_FILL_SEQUENCE_INVALID")
        return uuid5(NAMESPACE_URL, f"hope:paper:fill:{signal_id}:{sequence}")

    @staticmethod
    def pnl_event_id(position_id: UUID, sequence: int) -> UUID:
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError("PAPER_PNL_SEQUENCE_INVALID")
        return uuid5(NAMESPACE_URL, f"hope:paper:pnl:{position_id}:{sequence}")
