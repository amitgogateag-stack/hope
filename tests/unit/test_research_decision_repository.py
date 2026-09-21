from datetime import UTC, datetime
from uuid import uuid4

import pytest

from hope.application.experiments.comparisons import research_comparison_fingerprint
from hope.infrastructure.repositories.research_comparisons import (
    SqlAlchemyResearchComparisonRepository,
)
from hope.infrastructure.repositories.research_decisions import (
    SqlAlchemyResearchDecisionRepository,
)


class Rows:
    def __init__(self, row: dict | None) -> None:
        self._row = row

    def mappings(self):
        return self

    def one_or_none(self):
        return self._row


class Connection:
    def __init__(self, *rows: dict | None) -> None:
        self._rows = list(rows)

    def execute(self, statement):
        return Rows(self._rows.pop(0))


def evidence_rows() -> tuple[dict, dict]:
    control_run_id, variant_run_id = uuid4(), uuid4()
    canonical_comparison = {"status": "RECORDED", "metric": "1.0"}
    comparison_fingerprint = research_comparison_fingerprint(canonical_comparison)
    comparison = {
        "variant_experiment_id": "EXP-VARIANT",
        "control_run_id": control_run_id,
        "variant_run_id": variant_run_id,
        "control_result_fingerprint": "a" * 64,
        "variant_result_fingerprint": "b" * 64,
        "comparison_fingerprint": comparison_fingerprint,
        "canonical_comparison": canonical_comparison,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    comparison["comparison_id"] = (
        SqlAlchemyResearchComparisonRepository.deterministic_id(
            variant_experiment_id=comparison["variant_experiment_id"],
            control_run_id=comparison["control_run_id"],
            variant_run_id=comparison["variant_run_id"],
            control_result_fingerprint=comparison["control_result_fingerprint"],
            variant_result_fingerprint=comparison["variant_result_fingerprint"],
            comparison_fingerprint=comparison["comparison_fingerprint"],
        )
    )
    decision = {
        "decision_id": "DECISION-1",
        "variant_experiment_id": comparison["variant_experiment_id"],
        "control_run_id": control_run_id,
        "variant_run_id": variant_run_id,
        "comparison_id": comparison["comparison_id"],
        "comparison_fingerprint": comparison_fingerprint,
        "decision": "INCONCLUSIVE",
        "rationale": "Evidence does not yet support a stronger conclusion",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    return decision, comparison


def read(decision: dict, comparison: dict | None):
    return SqlAlchemyResearchDecisionRepository(
        Connection(decision, comparison)
    ).get(decision["decision_id"])


def test_decision_repository_rejects_missing_stored_comparison() -> None:
    decision, _ = evidence_rows()

    with pytest.raises(
        ValueError,
        match="RESEARCH_DECISION_STORED_COMPARISON_MISSING",
    ):
        read(decision, None)


def test_decision_repository_rejects_mismatched_stored_comparison_id() -> None:
    decision, comparison = evidence_rows()
    decision["comparison_id"] = uuid4()

    with pytest.raises(
        ValueError,
        match="RESEARCH_DECISION_STORED_COMPARISON_ID_MISMATCH",
    ):
        read(decision, comparison)


def test_decision_repository_rejects_mismatched_stored_comparison_fingerprint() -> None:
    decision, comparison = evidence_rows()
    decision["comparison_fingerprint"] = "f" * 64

    with pytest.raises(
        ValueError,
        match="RESEARCH_DECISION_STORED_COMPARISON_FINGERPRINT_MISMATCH",
    ):
        read(decision, comparison)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("decision_id", " DECISION-1 ", "RESEARCH_DECISION_IDENTITY_NOT_CANONICAL"),
        ("variant_experiment_id", " EXP-VARIANT ", "RESEARCH_DECISION_IDENTITY_NOT_CANONICAL"),
        ("rationale", " Evidence does not yet support a stronger conclusion ", "RESEARCH_DECISION_RATIONALE_NOT_CANONICAL"),
    ],
)
def test_decision_repository_revalidates_stored_definition(
    field: str,
    value: str,
    error: str,
) -> None:
    decision, comparison = evidence_rows()
    decision[field] = value
    if field == "variant_experiment_id":
        comparison["variant_experiment_id"] = value
        comparison["comparison_id"] = SqlAlchemyResearchComparisonRepository.deterministic_id(
            variant_experiment_id=value,
            control_run_id=comparison["control_run_id"],
            variant_run_id=comparison["variant_run_id"],
            control_result_fingerprint=comparison["control_result_fingerprint"],
            variant_result_fingerprint=comparison["variant_result_fingerprint"],
            comparison_fingerprint=comparison["comparison_fingerprint"],
        )
        decision["comparison_id"] = comparison["comparison_id"]

    with pytest.raises(ValueError, match=error):
        read(decision, comparison)
