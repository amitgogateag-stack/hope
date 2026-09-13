from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from hope.domain.risk.models import RiskAssessment, RiskDecision


def test_risk_assessment_rejects_non_finite_approved_quantity():
    with pytest.raises(ValidationError, match="RISK_APPROVED_QUANTITY_MUST_BE_FINITE"):
        RiskAssessment(
            signal_id=uuid4(),
            decision=RiskDecision.APPROVE,
            reason_code="OK",
            approved_quantity=Decimal("Infinity"),
        )
