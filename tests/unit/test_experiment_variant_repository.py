from datetime import UTC, datetime

import pytest

from hope.infrastructure.repositories.experiment_variants import (
    SqlAlchemyExperimentVariantRepository,
)


class Rows:
    def __init__(self, row: dict | None) -> None:
        self._row = row

    def mappings(self):
        return self

    def one_or_none(self):
        return self._row


class Connection:
    def __init__(self, row: dict | None) -> None:
        self._row = row

    def execute(self, statement):
        return Rows(self._row)


def variant_row() -> dict:
    return {
        "control_experiment_id": "EXP-CONTROL",
        "variant_experiment_id": "EXP-VARIANT",
        "variant_label": "lookback-variant",
        "predeclared_at": datetime(2026, 1, 1, tzinfo=UTC),
    }


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        (
            "control_experiment_id",
            " EXP-CONTROL ",
            "RESEARCH_VARIANT_EXPERIMENT_ID_NOT_CANONICAL",
        ),
        (
            "variant_label",
            " lookback-variant ",
            "RESEARCH_VARIANT_LABEL_NOT_CANONICAL",
        ),
        (
            "control_experiment_id",
            "EXP-VARIANT",
            "RESEARCH_VARIANT_CONTROL_MUST_DIFFER",
        ),
    ],
)
def test_variant_repository_revalidates_stored_definition(
    field: str,
    value: str,
    error: str,
) -> None:
    row = variant_row()
    row[field] = value
    repository = SqlAlchemyExperimentVariantRepository(Connection(row))

    with pytest.raises(ValueError, match=error):
        repository.get(row["variant_experiment_id"])
