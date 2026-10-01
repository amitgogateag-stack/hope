from types import SimpleNamespace
from uuid import uuid4

from hope.application.paper.effects import PaperEffect, PaperEffectType
from hope.infrastructure.scheduling.paper_lineage import verify_paper_recovery_lineage


def _effect(job_run_id, effect_type, entity_id):
    return PaperEffect(
        effect_id=uuid4(),
        job_run_id=job_run_id,
        effect_type=effect_type,
        entity_id=entity_id,
        payload_hash="0" * 64,
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


def test_verifier_rejects_non_filled_terminal_shape() -> None:
    job_run_id = uuid4()
    signal_id = uuid4()
    effects = (
        _effect(job_run_id, PaperEffectType.SIGNAL, signal_id),
        _effect(job_run_id, PaperEffectType.RISK, signal_id),
        _effect(job_run_id, PaperEffectType.ORDER, uuid4()),
        _effect(job_run_id, PaperEffectType.REJECTION, uuid4()),
    )

    assert verify_paper_recovery_lineage(SimpleNamespace(), effects) is False
