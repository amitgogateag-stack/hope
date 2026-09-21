from datetime import UTC, datetime
from uuid import uuid4

import pytest

from hope.domain.strategy.candidates import (
    StrategyCandidateClassification,
    StrategyCandidateState,
    StrategyMarket,
)
from hope.infrastructure.repositories.strategy_candidates import (
    SqlAlchemyStrategyCandidateRepository,
)


class Rows:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class Connection:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def execute(self, statement):
        return Rows(self._rows)


def classification_row(*, current: bool = False) -> dict:
    definition = StrategyCandidateClassification(
        strategy_version_id=uuid4(),
        markets=frozenset({StrategyMarket.INDIA, StrategyMarket.USA}),
        state=StrategyCandidateState.RESEARCH,
        rationale="Initial dual-market research registration",
    )
    row = {
        "classification_id": SqlAlchemyStrategyCandidateRepository.deterministic_id(
            definition
        ),
        "classification_sequence": 1,
        "strategy_version_id": definition.strategy_version_id,
        "markets": [market.value for market in sorted(definition.markets)],
        "state": definition.state.value,
        "research_decision_id": definition.research_decision_id,
        "rationale": definition.rationale,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    if current:
        row["family"] = "TEST_FAMILY"
    return row


def test_candidate_history_rejects_nondeterministic_stored_id() -> None:
    row = classification_row()
    row["classification_id"] = uuid4()
    repository = SqlAlchemyStrategyCandidateRepository(Connection([row]))

    with pytest.raises(
        ValueError,
        match="STRATEGY_CANDIDATE_STORED_ID_NOT_DETERMINISTIC",
    ):
        repository.history(row["strategy_version_id"])


def test_current_candidates_reject_nondeterministic_stored_id() -> None:
    row = classification_row(current=True)
    row["classification_id"] = uuid4()
    repository = SqlAlchemyStrategyCandidateRepository(Connection([row]))

    with pytest.raises(
        ValueError,
        match="STRATEGY_CANDIDATE_STORED_ID_NOT_DETERMINISTIC",
    ):
        repository.current()


def test_candidate_history_revalidates_research_decision_requirement() -> None:
    row = classification_row()
    row["state"] = StrategyCandidateState.OPERATIONAL_CANDIDATE.value
    repository = SqlAlchemyStrategyCandidateRepository(Connection([row]))

    with pytest.raises(
        ValueError,
        match="STRATEGY_CANDIDATE_RESEARCH_DECISION_REQUIRED",
    ):
        repository.history(row["strategy_version_id"])
