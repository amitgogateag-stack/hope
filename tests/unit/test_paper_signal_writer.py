from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper import PaperCycleContext, PaperEffectType, PaperSignalWriter, paper_signal_payload_hash
from hope.domain.signal.models import Signal, SignalType


UTC = timezone.utc
INPUTS_HASH = "a" * 64


class FakePaperSignalPersistence:
    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.calls = []

    def persist(self, effect, signal) -> bool:
        self.calls.append((effect, signal))
        return self.result


def make_context(minute: int = 0) -> PaperCycleContext:
    return PaperCycleContext(
        create_scheduled_job_run(
            "paper-signal-writer",
            datetime(2026, 9, 9, 22, minute, tzinfo=UTC),
        )
    )


def make_signal(
    context: PaperCycleContext,
    *,
    instrument_id=None,
    strategy_version: str = "strategy-v1",
    decision_time: datetime | None = None,
    conviction: Decimal = Decimal("0.5"),
) -> Signal:
    instrument = instrument_id or uuid4()
    decision = decision_time or datetime(2026, 9, 9, 21, 55, tzinfo=UTC)
    signal_id = context.signal_id(
        instrument_id=instrument,
        strategy_version=strategy_version,
        decision_time=decision,
        signal_type=SignalType.ENTRY,
        conviction=conviction,
        inputs_hash=INPUTS_HASH,
    )
    return Signal(
        signal_id=signal_id,
        instrument_id=instrument,
        strategy_version=strategy_version,
        decision_time=decision,
        signal_type=SignalType.ENTRY,
        conviction=conviction,
        inputs_hash=INPUTS_HASH,
    )


def test_paper_signal_writer_records_signal_effect_with_job_lineage() -> None:
    context = make_context()
    signal = make_signal(context)
    repository = FakePaperSignalPersistence()
    writer = PaperSignalWriter(repository)

    assert writer.record(context, signal) is True
    assert len(repository.calls) == 1
    effect, persisted_signal = repository.calls[0]
    assert persisted_signal == signal
    assert effect.job_run_id == context.job_run.job_run_id
    assert effect.effect_type is PaperEffectType.SIGNAL
    assert effect.entity_id == signal.signal_id
    assert effect.payload_hash == paper_signal_payload_hash(signal)


def test_paper_signal_writer_preserves_repository_duplicate_result() -> None:
    context = make_context()
    signal = make_signal(context)
    repository = FakePaperSignalPersistence(result=False)

    assert PaperSignalWriter(repository).record(context, signal) is False
    assert len(repository.calls) == 1


def test_paper_signal_writer_rejects_non_deterministic_signal_id() -> None:
    context = make_context()
    valid = make_signal(context)
    invalid = valid.model_copy(update={"signal_id": uuid4()})
    repository = FakePaperSignalPersistence()

    with pytest.raises(ValueError, match="PAPER_SIGNAL_IDENTITY_MISMATCH"):
        PaperSignalWriter(repository).record(context, invalid)
    assert repository.calls == []


def test_paper_signal_payload_hash_canonicalizes_equivalent_signal_content() -> None:
    context = make_context()
    instrument_id = uuid4()
    utc_time = datetime(2026, 9, 9, 21, 55, tzinfo=UTC)
    offset_time = utc_time.astimezone(timezone(timedelta(hours=-4)))
    first = make_signal(
        context,
        instrument_id=instrument_id,
        strategy_version="strategy-v1",
        decision_time=utc_time,
        conviction=Decimal("0.50"),
    )
    second = make_signal(
        context,
        instrument_id=instrument_id,
        strategy_version=" strategy-v1 ",
        decision_time=offset_time,
        conviction=Decimal("0.5000"),
    )

    assert first.signal_id == second.signal_id
    assert paper_signal_payload_hash(first) == paper_signal_payload_hash(second)
