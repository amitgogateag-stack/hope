from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import CHAR, Column, DateTime, MetaData, String, Table, Uuid, create_engine

from hope.infrastructure.repositories.experiments import ExperimentRecord, SqlAlchemyExperimentRepository


HASH = "a" * 64


def make_experiment() -> ExperimentRecord:
    return ExperimentRecord(
        experiment_id="EXP-TEST-001",
        hypothesis="A deterministic test hypothesis.",
        strategy_version_id=uuid4(),
        dataset_version_id=uuid4(),
        universe_version_id=uuid4(),
        configuration_hash=HASH,
        environment="RESEARCH",
        status="CREATED",
    )


def create_test_schema(connection):
    metadata = MetaData()
    Table(
        "experiments", metadata,
        Column("experiment_id", String, primary_key=True),
        Column("hypothesis", String, nullable=False),
        Column("strategy_version_id", Uuid, nullable=False),
        Column("dataset_version_id", Uuid, nullable=False),
        Column("universe_version_id", Uuid, nullable=False),
        Column("configuration_hash", CHAR(64), nullable=False),
        Column("environment", String, nullable=False),
        Column("status", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    Table(
        "experiment_invalidations", metadata,
        Column("experiment_id", String, primary_key=True),
        Column("reason", String, nullable=False),
        Column("invalidated_at", DateTime(timezone=True), nullable=False),
    )
    metadata.create_all(connection)


def test_experiment_round_trip_and_invalidation_are_append_only() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        create_test_schema(connection)
        repo = SqlAlchemyExperimentRepository(connection)
        experiment = make_experiment()
        repo.create(experiment)
        stored = repo.get(experiment.experiment_id)
        assert stored is not None
        assert stored.model_copy(update={"created_at": None}) == experiment
        assert stored.created_at is not None

        repo.invalidate(experiment.experiment_id, "MIXED_VINTAGE")
        stored_after = repo.get(experiment.experiment_id)
        assert stored_after is not None
        assert stored_after.model_copy(update={"created_at": None}) == experiment


@pytest.mark.parametrize(
    ("field_name", "value", "error_code"),
    [
        ("experiment_id", "   ", "EXPERIMENT_ID_REQUIRED"),
        ("experiment_id", " EXP-TEST-001", "EXPERIMENT_ID_NOT_CANONICAL"),
        ("experiment_id", "EXP-TEST-001 ", "EXPERIMENT_ID_NOT_CANONICAL"),
        ("hypothesis", "   ", "EXPERIMENT_HYPOTHESIS_REQUIRED"),
        ("hypothesis", " A deterministic test hypothesis.", "EXPERIMENT_HYPOTHESIS_NOT_CANONICAL"),
        ("hypothesis", "A deterministic test hypothesis. ", "EXPERIMENT_HYPOTHESIS_NOT_CANONICAL"),
    ],
)
def test_experiment_record_requires_canonical_durable_text(
    field_name,
    value,
    error_code,
) -> None:
    values = make_experiment().model_dump()
    values[field_name] = value

    with pytest.raises(ValidationError, match=error_code):
        ExperimentRecord(**values)


@pytest.mark.parametrize("reason", [" MIXED_VINTAGE", "MIXED_VINTAGE "])
def test_experiment_invalidation_reason_must_be_canonical(reason) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        create_test_schema(connection)
        repo = SqlAlchemyExperimentRepository(connection)
        experiment = make_experiment()
        repo.create(experiment)

        with pytest.raises(
            ValueError,
            match="EXPERIMENT_INVALIDATION_REASON_NOT_CANONICAL",
        ):
            repo.invalidate(experiment.experiment_id, reason)
