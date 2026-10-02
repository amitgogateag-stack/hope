from datetime import UTC, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid4, uuid5

from hope.application.paper.effects import PaperEffect, PaperEffectType
from hope.application.paper.risk import paper_risk_payload_hash
from hope.application.paper.terminals import paper_terminal_payload_hash
from hope.domain.execution.models import (
    Environment,
    ExecutionCancellation,
    ExecutionRejection,
)
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.infrastructure.scheduling.paper_lineage import verify_paper_recovery_lineage


def _effect(job_run_id, effect_type, entity_id, payload_hash="0" * 64):
    return PaperEffect(
        effect_id=uuid5(NAMESPACE_URL, f"hope:paper:effect:{effect_type.value}:{entity_id}"),
        job_run_id=job_run_id,
        effect_type=effect_type,
        entity_id=entity_id,
        payload_hash=payload_hash,
    )


def _approved_risk(signal_id):
    return RiskAssessment(
        signal_id=signal_id,
        decision=RiskDecision.APPROVE,
        reason_code="TEST_APPROVED",
        approved_quantity=Decimal("10"),
    )


def _risk_row(assessment):
    return {
        "decision": assessment.decision.value,
        "reason_code": assessment.reason_code,
        "approved_quantity": assessment.approved_quantity,
    }


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
    risk = _approved_risk(signal_id)
    effects = (
        _effect(job_run_id, PaperEffectType.SIGNAL, signal_id),
        _effect(
            job_run_id,
            PaperEffectType.RISK,
            signal_id,
            paper_risk_payload_hash(risk),
        ),
        _effect(job_run_id, PaperEffectType.ORDER, order_id),
        _effect(job_run_id, PaperEffectType.FILL, fill_id),
        _effect(job_run_id, PaperEffectType.PNL, pnl_event_id),
    )
    return effects, signal_id, order_id, risk


def test_verifier_accepts_one_coherent_filled_execution_lineage() -> None:
    effects, signal_id, order_id, risk = _filled_effects()
    position_id = uuid4()
    connection = _LineageConnection(
        (
            _risk_row(risk),
            {"signal_id": signal_id},
            {"order_id": order_id},
            {"position_id": position_id},
            {"opened_from_signal_id": signal_id},
        )
    )

    assert verify_paper_recovery_lineage(connection, effects) is True


def test_verifier_rejects_fill_linked_to_different_order() -> None:
    effects, signal_id, _, risk = _filled_effects()
    connection = _LineageConnection(
        (
            _risk_row(risk),
            {"signal_id": signal_id},
            {"order_id": uuid4()},
        )
    )

    assert verify_paper_recovery_lineage(connection, effects) is False


def test_verifier_rejects_pnl_position_from_different_signal() -> None:
    effects, signal_id, order_id, risk = _filled_effects()
    position_id = uuid4()
    connection = _LineageConnection(
        (
            _risk_row(risk),
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
    risk = _approved_risk(signal_id)
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
        _effect(
            job_run_id,
            PaperEffectType.RISK,
            signal_id,
            paper_risk_payload_hash(risk),
        ),
        _effect(job_run_id, PaperEffectType.ORDER, order_id),
        _effect(
            job_run_id,
            terminal_type,
            order_id,
            paper_terminal_payload_hash(outcome),
        ),
    )
    return effects, outcome, risk


def _terminal_rows(outcome, risk):
    cancelled_quantity = (
        outcome.cancelled_quantity if isinstance(outcome, ExecutionCancellation) else None
    )
    return (
        _risk_row(risk),
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
    effects, outcome, risk = _non_fill_effects(PaperEffectType.REJECTION)

    assert verify_paper_recovery_lineage(
        _LineageConnection(_terminal_rows(outcome, risk)), effects
    ) is True


def test_verifier_accepts_coherent_cancellation_lineage() -> None:
    effects, outcome, risk = _non_fill_effects(PaperEffectType.CANCELLATION)

    assert verify_paper_recovery_lineage(
        _LineageConnection(_terminal_rows(outcome, risk)), effects
    ) is True


def test_verifier_rejects_terminal_payload_mismatch() -> None:
    effects, outcome, risk = _non_fill_effects(PaperEffectType.REJECTION)
    corrupted = effects[:-1] + (
        _effect(
            effects[-1].job_run_id,
            PaperEffectType.REJECTION,
            outcome.order_id,
            "f" * 64,
        ),
    )

    assert verify_paper_recovery_lineage(
        _LineageConnection(_terminal_rows(outcome, risk)), corrupted
    ) is False


def test_verifier_rejects_risk_effect_without_matching_assessment_payload() -> None:
    effects, signal_id, order_id, risk = _filled_effects()
    corrupted = effects[:1] + (
        _effect(
            effects[1].job_run_id,
            PaperEffectType.RISK,
            signal_id,
            "f" * 64,
        ),
    ) + effects[2:]
    position_id = uuid4()
    connection = _LineageConnection(
        (
            _risk_row(risk),
            {"signal_id": signal_id},
            {"order_id": order_id},
            {"position_id": position_id},
            {"opened_from_signal_id": signal_id},
        )
    )

    assert verify_paper_recovery_lineage(connection, corrupted) is False


def test_verifier_rejects_missing_durable_risk_assessment() -> None:
    effects, _, _, _ = _filled_effects()

    assert verify_paper_recovery_lineage(_LineageConnection((None,)), effects) is False


def test_verifier_rejects_non_approved_durable_risk_assessment() -> None:
    effects, signal_id, _, _ = _filled_effects()
    rejected = RiskAssessment(
        signal_id=signal_id,
        decision=RiskDecision.REJECT,
        reason_code="TEST_REJECTED",
        approved_quantity=Decimal("0"),
    )
    effects = effects[:1] + (
        _effect(
            effects[1].job_run_id,
            PaperEffectType.RISK,
            signal_id,
            paper_risk_payload_hash(rejected),
        ),
    ) + effects[2:]

    assert verify_paper_recovery_lineage(
        _LineageConnection((_risk_row(rejected),)), effects
    ) is False
