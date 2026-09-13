from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper.context import PaperCycleContext
from hope.application.paper.effects import PaperEffectType
from hope.application.paper.risk import PaperRiskWriter, paper_risk_payload_hash
from hope.domain.risk.models import RiskAssessment, RiskDecision


class _Repository:
    def __init__(self) -> None:
        self.calls = []

    def persist(self, effect, assessment) -> bool:
        self.calls.append((effect, assessment))
        return True


def context() -> PaperCycleContext:
    return PaperCycleContext(
        create_scheduled_job_run(
            "paper-risk",
            datetime(2026, 9, 13, 15, 0, tzinfo=timezone.utc),
        )
    )


def assessment(*, decision=RiskDecision.APPROVE, quantity=Decimal("2"), reason="OK"):
    return RiskAssessment(
        signal_id=uuid4(),
        decision=decision,
        reason_code=reason,
        approved_quantity=quantity,
    )


def test_paper_risk_writer_records_deterministic_risk_effect():
    repository = _Repository()
    writer = PaperRiskWriter(repository)
    value = assessment()

    assert writer.record(context(), value) is True

    effect, recorded = repository.calls[0]
    assert recorded == value
    assert effect.effect_type is PaperEffectType.RISK
    assert effect.entity_id == value.signal_id
    assert effect.payload_hash == paper_risk_payload_hash(value)


def test_paper_risk_payload_hash_changes_with_decision_material():
    signal_id = uuid4()
    approved = RiskAssessment(
        signal_id=signal_id,
        decision=RiskDecision.APPROVE,
        reason_code="OK",
        approved_quantity=Decimal("2"),
    )
    rejected = RiskAssessment(
        signal_id=signal_id,
        decision=RiskDecision.REJECT,
        reason_code="LIMIT",
        approved_quantity=Decimal("0"),
    )

    assert paper_risk_payload_hash(approved) != paper_risk_payload_hash(rejected)


def test_paper_risk_payload_hash_rejects_noncanonical_reason():
    value = assessment(reason=" OK ")
    with pytest.raises(ValueError, match="PAPER_RISK_REASON_CODE_NOT_CANONICAL"):
        paper_risk_payload_hash(value)
