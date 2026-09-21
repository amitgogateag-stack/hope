import pytest

from hope.application.experiments.evaluation_protocol import (
    REQUIRED_EVALUATION_STAGES,
    research_evaluation_plan_hash,
)
from hope.infrastructure.repositories.research_evaluation_plans import (
    SqlAlchemyResearchEvaluationPlanRepository,
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


def protocol() -> dict:
    return {stage: {"enabled": True} for stage in REQUIRED_EVALUATION_STAGES}


def test_evaluation_plan_repository_rejects_tampered_stored_protocol() -> None:
    canonical_protocol = protocol()
    row = {
        "variant_experiment_id": "EXP-VARIANT",
        "protocol_hash": research_evaluation_plan_hash(canonical_protocol),
        "canonical_protocol": {**canonical_protocol, "walk_forward": {"folds": 99}},
        "predeclared_at": None,
    }
    repository = SqlAlchemyResearchEvaluationPlanRepository(Connection(row))

    with pytest.raises(
        ValueError,
        match="RESEARCH_EVALUATION_PLAN_STORED_HASH_MISMATCH",
    ):
        repository.get(row["variant_experiment_id"])


def test_evaluation_plan_repository_rejects_incomplete_stored_protocol() -> None:
    canonical_protocol = protocol()
    canonical_protocol.pop("walk_forward")
    row = {
        "variant_experiment_id": "EXP-VARIANT",
        "protocol_hash": research_evaluation_plan_hash(canonical_protocol),
        "canonical_protocol": canonical_protocol,
        "predeclared_at": None,
    }
    repository = SqlAlchemyResearchEvaluationPlanRepository(Connection(row))

    with pytest.raises(
        ValueError,
        match="RESEARCH_EVALUATION_REQUIRED_STAGES_MISSING:walk_forward",
    ):
        repository.get(row["variant_experiment_id"])
