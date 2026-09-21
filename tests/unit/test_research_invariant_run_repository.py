from uuid import uuid4

import pytest

from hope.application.validation.research_invariants import _fingerprint
from hope.infrastructure.repositories.research_invariant_runs import (
    SqlAlchemyResearchInvariantRunRepository,
)


class Rows:
    def __init__(self, row: dict) -> None:
        self._row = row

    def mappings(self):
        return self

    def one_or_none(self):
        return self._row


class Connection:
    def __init__(self, row: dict) -> None:
        self._row = row

    def execute(self, statement):
        return Rows(self._row)


def test_invariant_run_repository_rejects_tampered_stored_results() -> None:
    canonical_results = [
        {
            "invariant_id": "INVARIANT-001",
            "status": "PASS",
            "message": "member count check",
        }
    ]
    row = {
        "research_run_id": uuid4(),
        "context_fingerprint": "a" * 64,
        "result_fingerprint": _fingerprint(canonical_results),
        "canonical_results": [
            {
                "invariant_id": "INVARIANT-001",
                "status": "FAIL",
                "message": "tampered",
            }
        ],
        "created_at": None,
    }
    repository = SqlAlchemyResearchInvariantRunRepository(Connection(row))

    with pytest.raises(
        ValueError,
        match="RESEARCH_INVARIANT_STORED_RESULT_FINGERPRINT_MISMATCH",
    ):
        repository.get(row["research_run_id"])
