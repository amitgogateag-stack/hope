from datetime import UTC, datetime
from uuid import uuid4

import pytest

from hope.infrastructure.repositories.market_intelligence_reviews import (
    SqlAlchemyIntelligenceReviewRepository,
)


class Rows:
    def __init__(self, row): self.row = row
    def mappings(self): return self
    def one_or_none(self): return self.row


class Connection:
    def __init__(self, row): self.row = row
    def execute(self, statement): return Rows(self.row)


@pytest.mark.parametrize(
    ("field", "value"),
    [("policy_version", " policy-v1 "), ("rationale", " cleared after review ")],
)
def test_review_repository_rejects_noncanonical_stored_text(field: str, value: str) -> None:
    row = {
        "resolution_id": uuid4(),
        "assessment_id": uuid4(),
        "policy_version": "policy-v1",
        "outcome": "CLEARED",
        "rationale": "cleared after review",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    row[field] = value
    repository = SqlAlchemyIntelligenceReviewRepository(Connection(row))
    with pytest.raises(ValueError, match="INTELLIGENCE_REVIEW_TEXT_NOT_CANONICAL"):
        repository.get(row["assessment_id"], policy_version=row["policy_version"])
