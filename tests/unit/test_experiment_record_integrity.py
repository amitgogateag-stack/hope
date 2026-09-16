from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from hope.infrastructure.repositories.experiments import ExperimentRecord


BASE = {
    "strategy_version_id": uuid4(),
    "dataset_version_id": uuid4(),
    "universe_version_id": uuid4(),
    "configuration_hash": "a" * 64,
    "environment": "RESEARCH",
    "status": "CREATED",
}


@pytest.mark.parametrize("field", ["experiment_id", "hypothesis"])
def test_experiment_record_rejects_blank_identity_text(field: str) -> None:
    values = {"experiment_id": "exp-1", "hypothesis": "test hypothesis", **BASE}
    values[field] = "   "
    with pytest.raises(ValueError):
        ExperimentRecord(**values)


@pytest.mark.parametrize("environment", ["LIVE", "PAPER-LIVE", ""])
def test_experiment_record_rejects_unsupported_environment(environment: str) -> None:
    with pytest.raises(ValueError):
        ExperimentRecord(
            experiment_id="exp-1",
            hypothesis="test hypothesis",
            environment=environment,
            **{key: value for key, value in BASE.items() if key != "environment"},
        )


def test_experiment_record_rejects_non_created_status() -> None:
    with pytest.raises(ValueError):
        ExperimentRecord(
            experiment_id="exp-1",
            hypothesis="test hypothesis",
            status="RUNNING",
            **{key: value for key, value in BASE.items() if key != "status"},
        )


@pytest.mark.parametrize(
    "values",
    [
        {"invalidated_at": datetime(2026, 1, 2, tzinfo=timezone.utc)},
        {"invalidation_reason": "BAD_DATA"},
    ],
)
def test_experiment_record_requires_complete_invalidation_state(values: dict) -> None:
    with pytest.raises(ValidationError, match="EXPERIMENT_INVALIDATION_STATE_INCOMPLETE"):
        ExperimentRecord(
            experiment_id="exp-1",
            hypothesis="test hypothesis",
            **BASE,
            **values,
        )


@pytest.mark.parametrize("reason", ["", "   ", " BAD_DATA", "BAD_DATA "])
def test_experiment_record_requires_canonical_invalidation_reason(reason: str) -> None:
    with pytest.raises(ValidationError, match="EXPERIMENT_INVALIDATION_REASON"):
        ExperimentRecord(
            experiment_id="exp-1",
            hypothesis="test hypothesis",
            invalidated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            invalidation_reason=reason,
            **BASE,
        )
