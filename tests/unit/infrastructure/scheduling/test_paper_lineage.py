from datetime import UTC, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid4, uuid5

from hope.application.paper.effects import PaperEffect, PaperEffectType
from hope.application.paper.terminals import paper_terminal_payload_hash
from hope.domain.execution.models import (
    Environment,
    ExecutionCancellation,
    ExecutionRejection,
)
from hope.infrastructure.scheduling.paper_lineage import verify_paper_recovery_lineage


def _effect(job_run_id, effect_type, entity_id, payload_hash="0" * 64):
    return PaperEffect(
        effect_id=uuid5(NAMESPACE_URL, f"hope:paper:effect:{effect_type.value}:{entity_id}"),
        job_run_id=job_run_id,
        effect_type=effect_type,
        entity_id=entity_id,
        payload_hash=payload_hash,
    )


class _MappingsResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def one_or_none(self):
        return self._row


class _LineageConnection:
    def __init__(self, rows):
        self._rows = iter(rows)

    def execute(self, statement):
        return _MappingsResult(next(self._rows))


def _filled_effects():
    job_run_id = uuid4()
    signal_id = uuid4()
    order_id = uuid4()
    fill_id = uuid4()
    pnl_event_id = uuid4()
    effects = (
        _effect(job_run_id, PaperEffectType.SIGNAL, signal_id),
        _effect(job_run_id, PaperEffectType.RISK, signal_id),
        _effect(job_run_id, PaperEffectType.ORDER, order_id),
        _effect(job_run_id, PaperEffectType.FILL, fill_id),
        _effect(job_run_id, PaperEffectType.PNL, pnl_event_id),
    )
    return effects, signal_id, order_id


def test_verifier_accepts_one_coherent_filled_execution_lineage() -> None:
    effects, signal_id, order_id = _filled_effects()
    position_id = uuid4()
    connection = _LineageConnection(
        (
            {"signal_id": signal_id},
            {"order_id": order_id},
            {"position_id": position_id},
            {"opened_from_signal_id": signal_id},
        )
    )

    assert verify_paper_recovery_lineage(connection, effects) is True


def test_verifier_rejects_fill_linked_to_different_order() -> None:
    effects, signal_id, _ = _filled_effects()
    connection = _LineageConnection(
        (
            {"signal_id": signal_id},
            {"order_id": uuid4()},
        )
    )

    assert verify_paper_recovery_lineage(connection, effects) is False


def test_verifier_rejects_pnl_position_from_different_signal() -> None:
    effects, signal_id, order_id = _filled_effects()
    position_id = uuid4()
    connection = _LineageConnection(
        (
            {"signal_id": signal_id},
            {"order_id": order_id},
            {"position_id": position_id},
            {"opened_from_signal_id": uuid4()},
        )
    )

    assert verify_paper_recovery_lineage(connection, effects) is False


def _non_fill_effects(terminal_type):
    job_run_id = uuid4()
    signal_id = uuid4()
    order_id = uuid4()
    instrument_id = uuid4()
    event_time = datetime(2026, 10, 2, 15, 0, tzinfo=UTC)
    if terminal_type is PaperEffectType.REJECTION:
        outcome = ExecutionRejection(
            order_id,
            signal_id,
            instrument_id,
            Environment.PAPER,
            "TEST_REJECTION",
            event_time,
        )
    else:
        outcome = ExecutionCancellation(
            order_id,
            signal_id,
            instrument_id,
            Environment.PAPER,
            "TEST_CANCELLATION",
            event_time,
            Decimal("10"),
        )
    effects = (
        _effect(job_run_id, PaperEffectType.SIGNAL, signal_id),
        _effect(job_run_id, PaperEffectType.RISK, signal_id),
        _effect(job_run_id, PaperEffectType.ORDER, order_id),
        _effect(
            job_run_id,
            terminal_type,
            order_id,
            paper_terminal_payload_hash(outcome),
        ),
    )
    return effects, outcome


def _terminal_rows(outcome):
    cancelled_quantity = (
        outcome.cancelled_quantity if isinstance(outcome, ExecutionCancellation) else None
    )
    return (
        {
            "signal_id": outcome.signal_id,
            "instrument_id": outcome.instrument_id,
            "environment": Environment.PAPER.value,
        },
        {
            "outcome": (
                "CANCELLED" if isinstance(outcome, ExecutionCancellation) else "REJECTED"
            ),
            "reason_code": outcome.reason_code,
            "event_time": (
                outcome.cancellation_time
                if isinstance(outcome, ExecutionCancellation)
                else outcome.rejection_time
            ),
            "cancelled_quantity": cancelled_quantity,
        },
    )


def test_verifier_accepts_coherent_rejection_lineage() -> None:
    effects, outcome = _non_fill_effects(PaperEffectType.REJECTION)

    assert verify_paper_recovery_lineage(
        _LineageConnection(_terminal_rows(outcome)), effects
    ) is True


def test_verifier_accepts_coherent_cancellation_lineage() -> None:
    effects, outcome = _non_fill_effects(PaperEffectType.CANCELLATION)

    assert verify_paper_recovery_lineage(
        _LineageConnection(_terminal_rows(outcome)), effects
    ) is True


def test_verifier_rejects_terminal_payload_mismatch() -> None:
    effects, outcome = _non_fill_effects(PaperEffectType.REJECTION)
    corrupted = effects[:-1] + (
        _effect(
            effects[-1].job_run_id,
            PaperEffectType.REJECTION,
            outcome.order_id,
            "f" * 64,
        ),
    )

    assert verify_paper_recovery_lineage(
        _LineageConnection(_terminal_rows(outcome)), corrupted
    ) is False
