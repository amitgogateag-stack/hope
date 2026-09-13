from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from hope.domain.risk.models import RiskAssessment, RiskDecision


def test_risk_assessment_rejects_non_finite_approved_quantity():
    with pytest.raises(ValidationError):
        RiskAssessment(
            signal_id=uuid4(),
            decision=RiskDecision.APPROVE,
            reason_code="OK",
            approved_quantity=Decimal("Infinity"),
        )


def test_risk_rejection_requires_zero_approved_quantity():
    with pytest.raises(ValidationError, match="RISK_REJECTION_REQUIRES_ZERO_QUANTITY"):
        RiskAssessment(
            signal_id=uuid4(),
            decision=RiskDecision.REJECT,
            reason_code="BLOCKED",
            approved_quantity=Decimal("1"),
        )


def test_risk_rejection_accepts_zero_approved_quantity():
    assessment = RiskAssessment(
        signal_id=uuid4(),
        decision=RiskDecision.REJECT,
        reason_code="BLOCKED",
        approved_quantity=Decimal("0"),
    )

    assert assessment.approved is False
    assert assessment.approved_quantity == 0


def test_risk_approval_requires_positive_approved_quantity():
    with pytest.raises(ValidationError, match="RISK_APPROVAL_REQUIRES_POSITIVE_QUANTITY"):
        RiskAssessment(
            signal_id=uuid4(),
            decision=RiskDecision.APPROVE,
            reason_code="OK",
            approved_quantity=Decimal("0"),
        )
