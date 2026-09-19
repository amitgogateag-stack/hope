from uuid import uuid4

import pytest
from pydantic import ValidationError

from hope.domain.market_intelligence.review import (
    IntelligenceReviewOutcome,
    IntelligenceReviewResolution,
)


def test_intelligence_review_resolution_is_explicit_and_canonical() -> None:
    resolution = IntelligenceReviewResolution(
        assessment_id=uuid4(),
        policy_version="hope.intelligence-policy.v1",
        outcome=IntelligenceReviewOutcome.CLEARED,
        rationale="Primary filing reviewed and entry block cleared",
    )
    assert resolution.outcome is IntelligenceReviewOutcome.CLEARED


@pytest.mark.parametrize("field", ["policy_version", "rationale"])
def test_intelligence_review_resolution_rejects_noncanonical_text(field: str) -> None:
    values = {
        "assessment_id": uuid4(),
        "policy_version": "hope.intelligence-policy.v1",
        "outcome": IntelligenceReviewOutcome.NO_ACTION_REQUIRED,
        "rationale": "No material action required",
    }
    values[field] = " bad "
    with pytest.raises(ValidationError):
        IntelligenceReviewResolution(**values)
