from uuid import uuid4

import pytest

from hope.application.experiments.research_runs import research_result_fingerprint
from hope.infrastructure.repositories.research_run_evidence import (
    ResearchRunEvidenceRecord,
    SqlAlchemyResearchRunEvidenceRepository,
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


def test_research_run_evidence_rejects_mismatched_fingerprint_before_persist() -> None:
    evidence = ResearchRunEvidenceRecord(
        research_run_id=uuid4(),
        result_fingerprint="a" * 64,
        canonical_result={"status": "RECORDED"},
    )
    repository = SqlAlchemyResearchRunEvidenceRepository(Connection({}))

    with pytest.raises(
        ValueError,
        match="RESEARCH_RUN_EVIDENCE_FINGERPRINT_MISMATCH",
    ):
        repository.persist(evidence)


def test_research_run_evidence_rejects_tampered_stored_result() -> None:
    canonical_result = {"status": "RECORDED"}
    _, result_fingerprint = research_result_fingerprint(canonical_result)
    row = {
        "research_run_id": uuid4(),
        "result_fingerprint": result_fingerprint,
        "canonical_result": {"status": "tampered"},
        "created_at": None,
    }
    repository = SqlAlchemyResearchRunEvidenceRepository(Connection(row))

    with pytest.raises(
        ValueError,
        match="RESEARCH_RUN_EVIDENCE_STORED_FINGERPRINT_MISMATCH",
    ):
        repository.get(row["research_run_id"])
