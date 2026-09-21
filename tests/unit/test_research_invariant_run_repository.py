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


@pytest.mark.parametrize(
    ("canonical_results", "error"),
    [
        ([{"invariant_id": "INVARIANT-001", "status": "UNKNOWN", "message": "x"}], "RESEARCH_INVARIANT_STATUS_INVALID"),
        ([{"invariant_id": " INVARIANT-001 ", "status": "PASS", "message": "x"}], "RESEARCH_INVARIANT_ID_NOT_CANONICAL"),
        ([{"invariant_id": "INVARIANT-001", "status": "PASS", "message": "x", "extra": "tampered"}], "RESEARCH_INVARIANT_RESULT_SHAPE_INVALID"),
        (
            [
                {"invariant_id": "INVARIANT-001", "status": "PASS", "message": "x"},
                {"invariant_id": "INVARIANT-001", "status": "FAIL", "message": "y"},
            ],
            "RESEARCH_INVARIANT_RESULT_DUPLICATE",
        ),
    ],
)
def test_invariant_run_repository_rejects_semantically_invalid_stored_results(
    canonical_results: list[dict[str, str]],
    error: str,
) -> None:
    row = {
        "research_run_id": uuid4(),
        "context_fingerprint": "a" * 64,
        "result_fingerprint": _fingerprint(canonical_results),
        "canonical_results": canonical_results,
        "created_at": None,
    }
    repository = SqlAlchemyResearchInvariantRunRepository(Connection(row))

    with pytest.raises(ValueError, match=error):
        repository.get(row["research_run_id"])
