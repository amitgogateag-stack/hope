from uuid import uuid4

import pytest

from hope.application.experiments.comparisons import research_comparison_fingerprint
from hope.infrastructure.repositories.research_comparisons import (
    SqlAlchemyResearchComparisonRepository,
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


def comparison_row() -> dict:
    control_run_id, variant_run_id = uuid4(), uuid4()
    canonical_comparison = {"status": "RECORDED", "metric": "1.0"}
    comparison_fingerprint = research_comparison_fingerprint(canonical_comparison)
    values = {
        "variant_experiment_id": "EXP-VARIANT",
        "control_run_id": control_run_id,
        "variant_run_id": variant_run_id,
        "control_result_fingerprint": "a" * 64,
        "variant_result_fingerprint": "b" * 64,
        "comparison_fingerprint": comparison_fingerprint,
        "canonical_comparison": canonical_comparison,
        "created_at": None,
    }
    values["comparison_id"] = SqlAlchemyResearchComparisonRepository.deterministic_id(
        **{
            key: values[key]
            for key in (
                "variant_experiment_id",
                "control_run_id",
                "variant_run_id",
                "control_result_fingerprint",
                "variant_result_fingerprint",
                "comparison_fingerprint",
            )
        }
    )
    return values


def read(row: dict):
    repository = SqlAlchemyResearchComparisonRepository(Connection(row))
    return repository.get_by_run_pair(
        row["variant_experiment_id"],
        row["control_run_id"],
        row["variant_run_id"],
    )


def test_comparison_repository_rejects_tampered_stored_comparison() -> None:
    row = comparison_row()
    row["canonical_comparison"] = {"status": "tampered"}

    with pytest.raises(
        ValueError,
        match="RESEARCH_COMPARISON_STORED_FINGERPRINT_MISMATCH",
    ):
        read(row)


def test_comparison_repository_rejects_nondeterministic_stored_id() -> None:
    row = comparison_row()
    row["comparison_id"] = uuid4()

    with pytest.raises(
        ValueError,
        match="RESEARCH_COMPARISON_STORED_ID_NOT_DETERMINISTIC",
    ):
        read(row)
