from datetime import UTC, datetime
from uuid import uuid4

import pytest

from hope.infrastructure.repositories.research_runs import (
    SqlAlchemyResearchRunRepository,
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


def run_row() -> dict:
    return {
        "research_run_id": uuid4(),
        "experiment_id": "EXP-VARIANT",
        "run_fingerprint": "a" * 64,
        "as_of": datetime(2026, 1, 1, tzinfo=UTC),
        "created_at": None,
    }


def test_research_run_get_rejects_nondeterministic_stored_id() -> None:
    row = run_row()
    repository = SqlAlchemyResearchRunRepository(Connection(row))

    with pytest.raises(
        ValueError,
        match="RESEARCH_RUN_STORED_ID_NOT_DETERMINISTIC",
    ):
        repository.get(row["research_run_id"])


def test_research_run_fingerprint_lookup_rejects_nondeterministic_stored_id() -> None:
    row = run_row()
    repository = SqlAlchemyResearchRunRepository(Connection(row))

    with pytest.raises(
        ValueError,
        match="RESEARCH_RUN_STORED_ID_NOT_DETERMINISTIC",
    ):
        repository.get_by_fingerprint(
            row["experiment_id"],
            row["run_fingerprint"],
        )
