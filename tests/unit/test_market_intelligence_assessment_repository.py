from datetime import UTC, datetime
from uuid import uuid4

import pytest

from hope.infrastructure.repositories.market_intelligence_assessments import (
    SqlAlchemyIntelligenceAssessmentRepository,
)


class Rows:
    def __init__(self, row): self.row = row
    def mappings(self): return self
    def one_or_none(self): return self.row


class Connection:
    def __init__(self, row): self.row = row
    def execute(self, statement): return Rows(self.row)


@pytest.mark.parametrize("policy_version", ["", " hope.intelligence-policy.v1 ", "hope.intelligence-policy.v1 "])
def test_assessment_repository_rejects_noncanonical_stored_policy(policy_version: str) -> None:
    row = {
        "assessment_id": uuid4(),
        "event_id": uuid4(),
        "policy_version": policy_version,
        "disposition": "OBSERVE_ONLY",
        "source_action": "OBSERVE",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    repository = SqlAlchemyIntelligenceAssessmentRepository(Connection(row))
    with pytest.raises(ValueError, match="INTELLIGENCE_ASSESSMENT_POLICY_NOT_CANONICAL"):
        repository.get_for_event(row["event_id"], policy_version=policy_version)


def test_assessment_repository_rejects_stored_disposition_mismatch() -> None:
    row = {
        "assessment_id": uuid4(),
        "event_id": uuid4(),
        "policy_version": "hope.intelligence-policy.v1",
        "disposition": "OBSERVE_ONLY",
        "source_action": "BLOCK_NEW_ENTRY",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    repository = SqlAlchemyIntelligenceAssessmentRepository(Connection(row))
    with pytest.raises(
        ValueError,
        match="INTELLIGENCE_ASSESSMENT_DISPOSITION_MISMATCH",
    ):
        repository.get_for_event(
            row["event_id"],
            policy_version=row["policy_version"],
        )
