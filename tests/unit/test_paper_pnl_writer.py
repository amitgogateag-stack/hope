from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper import PaperCycleContext, PaperEffectType, PaperPnLEvent, PaperPnLWriter, paper_pnl_payload_hash


UTC = timezone.utc


class FakePaperPnLPersistence:
    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.calls = []

    def persist(self, effect, event) -> bool:
        self.calls.append((effect, event))
        return self.result


def make_context() -> PaperCycleContext:
    return PaperCycleContext(
        create_scheduled_job_run("paper-pnl-writer", datetime(2026, 9, 9, 23, 30, tzinfo=UTC))
    )


def test_paper_pnl_writer_records_effect_with_job_lineage() -> None:
    context = make_context()
    position_id = uuid4()
    event = PaperPnLEvent(
        context.pnl_event_id(position_id, 0),
        position_id,
        Decimal("12.50"),
        datetime(2026, 9, 9, 23, 29, tzinfo=UTC),
    )
    repository = FakePaperPnLPersistence()

    assert PaperPnLWriter(repository).record(context, event, sequence=0) is True
    effect, persisted = repository.calls[0]
    assert persisted == event
    assert effect.job_run_id == context.job_run.job_run_id
    assert effect.effect_type is PaperEffectType.PNL
    assert effect.entity_id == event.pnl_event_id
    assert effect.payload_hash == paper_pnl_payload_hash(event)


def test_paper_pnl_writer_preserves_duplicate_result() -> None:
    context = make_context()
    position_id = uuid4()
    event = PaperPnLEvent(
        context.pnl_event_id(position_id, 1),
        position_id,
        Decimal("0"),
        datetime(2026, 9, 9, 23, 29, tzinfo=UTC),
    )
    assert PaperPnLWriter(FakePaperPnLPersistence(False)).record(context, event, sequence=1) is False


def test_paper_pnl_writer_rejects_non_deterministic_id() -> None:
    context = make_context()
    event = PaperPnLEvent(
        uuid4(),
        uuid4(),
        Decimal("1"),
        datetime(2026, 9, 9, 23, 29, tzinfo=UTC),
    )
    repository = FakePaperPnLPersistence()
    with pytest.raises(ValueError, match="PAPER_PNL_IDENTITY_MISMATCH"):
        PaperPnLWriter(repository).record(context, event, sequence=0)
    assert repository.calls == []


def test_paper_pnl_payload_hash_canonicalizes_decimal_and_timezone() -> None:
    context = make_context()
    position_id = uuid4()
    event_id = context.pnl_event_id(position_id, 2)
    utc_time = datetime(2026, 9, 9, 23, 29, tzinfo=UTC)
    first = PaperPnLEvent(event_id, position_id, Decimal("12.500"), utc_time)
    second = PaperPnLEvent(
        event_id,
        position_id,
        Decimal("12.50"),
        utc_time.astimezone(timezone(timedelta(hours=-4))),
    )
    assert paper_pnl_payload_hash(first) == paper_pnl_payload_hash(second)


def test_paper_pnl_identity_rejects_invalid_sequence() -> None:
    context = make_context()
    with pytest.raises(ValueError, match="PAPER_PNL_SEQUENCE_INVALID"):
        context.pnl_event_id(uuid4(), -1)
