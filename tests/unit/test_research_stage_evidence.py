from uuid import uuid4

import pytest

from hope.infrastructure.repositories.research_stage_evidence import (
    GENERIC_STAGE_EVIDENCE_STAGES,
    ResearchStageEvidenceRecord,
    SqlAlchemyResearchStageEvidenceRepository,
    build_research_stage_evidence_repositories,
    research_stage_evidence_fingerprint,
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


def test_stage_evidence_fingerprint_is_canonical():
    a = {"schema": "x", "folds": [{"id": "1", "metrics": {"pnl": "10"}}]}
    b = {"folds": [{"metrics": {"pnl": "10"}, "id": "1"}], "schema": "x"}
    assert research_stage_evidence_fingerprint(a) == research_stage_evidence_fingerprint(b)


def test_stage_evidence_record_accepts_only_generic_stage_evidence_stages():
    assert "walk_forward" in GENERIC_STAGE_EVIDENCE_STAGES
    assert "regression_invariants" not in GENERIC_STAGE_EVIDENCE_STAGES
    assert "historical_evaluation" not in GENERIC_STAGE_EVIDENCE_STAGES

    payload = {"schema": "hope.walk-forward-evidence.v1", "folds": [{"fold_id": "1"}]}
    ResearchStageEvidenceRecord(
        research_run_id=uuid4(),
        stage="walk_forward",
        result_fingerprint=research_stage_evidence_fingerprint(payload),
        canonical_result=payload,
    )

    with pytest.raises(ValueError, match="RESEARCH_STAGE_EVIDENCE_STAGE_INVALID"):
        ResearchStageEvidenceRecord(
            research_run_id=uuid4(),
            stage="regression_invariants",
            result_fingerprint=research_stage_evidence_fingerprint(payload),
            canonical_result=payload,
        )


def test_stage_evidence_repository_rejects_specialized_or_unknown_stage():
    with pytest.raises(ValueError, match="RESEARCH_STAGE_EVIDENCE_STAGE_INVALID"):
        SqlAlchemyResearchStageEvidenceRepository(object(), "regression_invariants")

    with pytest.raises(ValueError, match="RESEARCH_STAGE_EVIDENCE_STAGE_INVALID"):
        SqlAlchemyResearchStageEvidenceRepository(object(), "made_up")


def test_stage_evidence_fingerprint_requires_nonempty_mapping():
    with pytest.raises(ValueError, match="RESEARCH_STAGE_EVIDENCE_RESULT_REQUIRED"):
        research_stage_evidence_fingerprint({})


def test_stage_evidence_repository_rejects_tampered_stored_result():
    canonical_result = {"schema": "hope.walk-forward-evidence.v1", "folds": []}
    row = {
        "research_run_id": uuid4(),
        "stage": "walk_forward",
        "result_fingerprint": research_stage_evidence_fingerprint(canonical_result),
        "canonical_result": {"schema": "tampered", "folds": []},
        "created_at": None,
    }
    repository = SqlAlchemyResearchStageEvidenceRepository(
        Connection(row),
        "walk_forward",
    )

    with pytest.raises(
        ValueError,
        match="RESEARCH_STAGE_EVIDENCE_STORED_FINGERPRINT_MISMATCH",
    ):
        repository.get(row["research_run_id"])


def test_canonical_stage_repository_map_binds_every_derived_stage():
    invariant_repository = object()
    repositories = build_research_stage_evidence_repositories(
        object(),
        invariant_repository=invariant_repository,
    )

    assert repositories["regression_invariants"] is invariant_repository
    assert "historical_evaluation" not in repositories
    assert set(repositories) == {"regression_invariants", *GENERIC_STAGE_EVIDENCE_STAGES}
    for stage in GENERIC_STAGE_EVIDENCE_STAGES:
        assert repositories[stage].stage == stage


def test_canonical_stage_repository_map_requires_invariant_repository():
    with pytest.raises(
        ValueError,
        match="RESEARCH_INVARIANT_EVIDENCE_REPOSITORY_REQUIRED",
    ):
        build_research_stage_evidence_repositories(
            object(),
            invariant_repository=None,
        )
