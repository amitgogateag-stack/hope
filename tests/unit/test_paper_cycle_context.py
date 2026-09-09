from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper import PaperCycleContext
from hope.domain.signal.models import SignalType


UTC = timezone.utc


def make_context(hour: int = 20) -> PaperCycleContext:
    return PaperCycleContext(
        create_scheduled_job_run(
            "paper-cycle",
            datetime(2026, 9, 9, hour, 0, tzinfo=UTC),
        )
    )


def signal_id(context: PaperCycleContext, **overrides):
    values = {
        "instrument_id": uuid4(),
        "strategy_version": "1.0.0",
        "decision_time": datetime(2026, 9, 9, 20, 30, tzinfo=UTC),
        "signal_type": SignalType.ENTRY,
        "conviction": Decimal("0.50"),
        "inputs_hash": "a" * 64,
    }
    values.update(overrides)
    return context.signal_id(**values)


def test_paper_signal_identity_is_stable_across_cycle_runs_and_timezone_offsets() -> None:
    first = make_context(20)
    second = make_context(21)
    instrument_id = uuid4()

    first_id = signal_id(first, instrument_id=instrument_id)
    second_id = signal_id(
        second,
        instrument_id=instrument_id,
        decision_time=datetime(
            2026,
            9,
            9,
            22,
            30,
            tzinfo=timezone(timedelta(hours=2)),
        ),
        conviction=Decimal("0.5000"),
    )

    assert first.job_run.job_run_id != second.job_run.job_run_id
    assert first_id == second_id


def test_paper_signal_identity_changes_with_material_signal_content() -> None:
    context = make_context()
    instrument_id = uuid4()

    baseline = signal_id(context, instrument_id=instrument_id)
    changed = signal_id(
        context,
        instrument_id=instrument_id,
        conviction=Decimal("0.60"),
    )

    assert baseline != changed


def test_paper_order_and_fill_identity_are_deterministic() -> None:
    context = make_context()
    current_signal_id = signal_id(context)

    assert context.order_id(current_signal_id) == context.order_id(current_signal_id)
    assert context.fill_id(current_signal_id, 0) == context.fill_id(current_signal_id, 0)
    assert context.fill_id(current_signal_id, 0) != context.fill_id(current_signal_id, 1)


def test_paper_signal_identity_rejects_naive_decision_time() -> None:
    context = make_context()

    with pytest.raises(ValueError, match="PAPER_SIGNAL_DECISION_TIME_MUST_BE_TIMEZONE_AWARE"):
        signal_id(context, decision_time=datetime(2026, 9, 9, 20, 30))


def test_paper_signal_identity_rejects_blank_strategy_version() -> None:
    context = make_context()

    with pytest.raises(ValueError, match="PAPER_SIGNAL_STRATEGY_VERSION_REQUIRED"):
        signal_id(context, strategy_version="   ")


def test_paper_signal_identity_rejects_invalid_conviction() -> None:
    context = make_context()

    with pytest.raises(ValueError, match="PAPER_SIGNAL_CONVICTION_INVALID"):
        signal_id(context, conviction=Decimal("NaN"))
    with pytest.raises(ValueError, match="PAPER_SIGNAL_CONVICTION_INVALID"):
        signal_id(context, conviction=Decimal("1.01"))


def test_paper_signal_identity_rejects_invalid_inputs_hash() -> None:
    context = make_context()

    with pytest.raises(ValueError, match="PAPER_SIGNAL_INPUTS_HASH_INVALID"):
        signal_id(context, inputs_hash="A" * 64)


def test_paper_fill_identity_rejects_invalid_sequence() -> None:
    context = make_context()
    current_signal_id = signal_id(context)

    for invalid in (-1, True, 1.5):
        with pytest.raises(ValueError, match="PAPER_FILL_SEQUENCE_INVALID"):
            context.fill_id(current_signal_id, invalid)
