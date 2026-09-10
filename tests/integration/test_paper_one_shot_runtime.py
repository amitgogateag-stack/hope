import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from hope.application.jobs import JobRunStatus, create_scheduled_job_run
from hope.application.paper import PaperCycleOutcome
from hope.domain.strategy.models import ParameterSnapshot, Strategy
from hope.infrastructure.paper_runtime import (
    DurableUniversePaperStrategyDecision,
    PaperJobDefinition,
    PaperJobRegistry,
    run_paper_once,
)
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository


UTC = timezone.utc


class _Parameters(ParameterSnapshot):
    pass


class _NoTradeStrategy(Strategy):
    name = "paper-one-shot-no-trade"
    version = "v1"

    def __init__(self) -> None:
        self.calls = []

    def generate_signals(
        self,
        market_context,
        universe,
        universe_members,
        parameters,
        inputs_hash,
    ):
        self.calls.append(
            (market_context, universe, universe_members, parameters, inputs_hash)
        )
        return ()


def _engine():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    return create_engine(url)


def _prepare_job(engine, job_run) -> None:
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text("DELETE FROM job_runs WHERE job_key = :job_key AND scheduled_for = :scheduled_for"),
            {"job_key": job_run.job_key, "scheduled_for": job_run.scheduled_for},
        )


def _insert_durable_decision_inputs(engine, as_of):
    universe_id = uuid4()
    universe_version_id = uuid4()
    instrument_id = uuid4()
    dataset_id = uuid4()
    dataset_version_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                "VALUES (:instrument_id, :symbol, 'TEST', 'ACTIVE')"
            ),
            {"instrument_id": instrument_id, "symbol": f"PAPER-{instrument_id}"},
        )
        connection.execute(
            text("INSERT INTO universes(universe_id, name) VALUES (:universe_id, :name)"),
            {"universe_id": universe_id, "name": f"paper-{universe_id}"},
        )
        connection.execute(
            text(
                "INSERT INTO universe_versions("
                "universe_version_id, universe_id, version, pit_certified, declared_member_count"
                ") VALUES (:version_id, :universe_id, 'v1', TRUE, 1)"
            ),
            {"version_id": universe_version_id, "universe_id": universe_id},
        )
        connection.execute(
            text(
                "INSERT INTO universe_members(universe_version_id, instrument_id) "
                "VALUES (:version_id, :instrument_id)"
            ),
            {"version_id": universe_version_id, "instrument_id": instrument_id},
        )
        connection.execute(
            text(
                "INSERT INTO datasets(dataset_id, name, source, pit_certified) "
                "VALUES (:dataset_id, :name, 'TEST', TRUE)"
            ),
            {"dataset_id": dataset_id, "name": f"paper-{dataset_id}"},
        )
        connection.execute(
            text(
                "INSERT INTO dataset_versions("
                "dataset_version_id, dataset_id, version, vintage_label, immutable"
                ") VALUES (:dataset_version_id, :dataset_id, 'v1', 'paper-test', FALSE)"
            ),
            {"dataset_version_id": dataset_version_id, "dataset_id": dataset_id},
        )
        connection.execute(
            text(
                "INSERT INTO market_bars("
                "dataset_version_id, instrument_id, event_time, available_time, ingestion_time, "
                "open, high, low, close, volume"
                ") VALUES ("
                ":dataset_version_id, :instrument_id, :event_time, :available_time, :ingestion_time, "
                "100, 101, 99, 100, 1000"
                ")"
            ),
            {
                "dataset_version_id": dataset_version_id,
                "instrument_id": instrument_id,
                "event_time": as_of - timedelta(minutes=5),
                "available_time": as_of - timedelta(minutes=4),
                "ingestion_time": as_of - timedelta(minutes=3),
            },
        )
        connection.execute(
            text(
                "INSERT INTO market_bars("
                "dataset_version_id, instrument_id, event_time, available_time, ingestion_time, "
                "open, high, low, close, volume"
                ") VALUES ("
                ":dataset_version_id, :instrument_id, :event_time, :available_time, :ingestion_time, "
                "999, 999, 999, 999, 1000"
                ")"
            ),
            {
                "dataset_version_id": dataset_version_id,
                "instrument_id": instrument_id,
                "event_time": as_of - timedelta(minutes=2),
                "available_time": as_of - timedelta(minutes=1),
                "ingestion_time": as_of + timedelta(minutes=1),
            },
        )
        connection.execute(
            text(
                "UPDATE dataset_versions SET immutable = TRUE "
                "WHERE dataset_version_id = :dataset_version_id"
            ),
            {"dataset_version_id": dataset_version_id},
        )
    return universe_id, universe_version_id, instrument_id, dataset_version_id


@pytest.mark.integration
def test_run_paper_once_commits_successful_terminal_state() -> None:
    engine = _engine()
    job_run = create_scheduled_job_run(
        "paper-one-shot-success",
        datetime(2026, 9, 10, 13, 30, tzinfo=UTC),
    )
    _prepare_job(engine, job_run)
    completed_at = job_run.scheduled_for + timedelta(minutes=1)
    calls = []
    registry = PaperJobRegistry(
        [
            PaperJobDefinition(
                job_run.job_key,
                lambda runtime: calls.append(runtime.cycle.job_run.job_run_id),
            )
        ]
    )

    outcome = run_paper_once(
        engine,
        job_run,
        registry,
        now=lambda: completed_at,
    )

    assert outcome is PaperCycleOutcome.EXECUTED
    assert calls == [job_run.job_run_id]
    with engine.connect() as connection:
        record = SqlAlchemyJobRunRepository(connection).get_record(job_run.job_run_id)
        assert record is not None
        assert record.status is JobRunStatus.SUCCEEDED
        assert record.completed_at == completed_at
        assert record.failure_code is None


@pytest.mark.integration
def test_run_paper_once_commits_failed_terminal_state_before_reraising() -> None:
    engine = _engine()
    job_run = create_scheduled_job_run(
        "paper-one-shot-failure",
        datetime(2026, 9, 10, 13, 31, tzinfo=UTC),
    )
    _prepare_job(engine, job_run)
    completed_at = job_run.scheduled_for + timedelta(minutes=1)
    calls = []

    def fail(runtime) -> None:
        calls.append(runtime.cycle.job_run.job_run_id)
        raise ValueError("paper-one-shot-boom")

    registry = PaperJobRegistry([PaperJobDefinition(job_run.job_key, fail)])

    with pytest.raises(ValueError, match="paper-one-shot-boom"):
        run_paper_once(engine, job_run, registry, now=lambda: completed_at)

    assert calls == [job_run.job_run_id]
    with engine.connect() as connection:
        record = SqlAlchemyJobRunRepository(connection).get_record(job_run.job_run_id)
        assert record is not None
        assert record.status is JobRunStatus.FAILED
        assert record.completed_at == completed_at
        assert record.failure_code == "ValueError"

    outcome = run_paper_once(
        engine,
        job_run,
        registry,
        now=lambda: completed_at + timedelta(minutes=1),
    )
    assert outcome is PaperCycleOutcome.SKIPPED_TERMINAL
    assert calls == [job_run.job_run_id]


@pytest.mark.integration
def test_run_paper_once_rejects_unregistered_job_before_lifecycle_claim() -> None:
    engine = _engine()
    job_run = create_scheduled_job_run(
        "paper-one-shot-unregistered",
        datetime(2026, 9, 10, 13, 32, tzinfo=UTC),
    )
    _prepare_job(engine, job_run)

    with pytest.raises(RuntimeError, match="PAPER_JOB_NOT_REGISTERED"):
        run_paper_once(
            engine,
            job_run,
            PaperJobRegistry([]),
            now=lambda: job_run.scheduled_for + timedelta(minutes=1),
        )

    with engine.connect() as connection:
        assert SqlAlchemyJobRunRepository(connection).get_record(job_run.job_run_id) is None


@pytest.mark.integration
def test_durable_strategy_decision_loads_exact_universe_and_pit_market_data() -> None:
    engine = _engine()
    job_run = create_scheduled_job_run(
        "paper-one-shot-durable-inputs",
        datetime(2026, 9, 10, 13, 33, tzinfo=UTC),
    )
    _prepare_job(engine, job_run)
    universe_id, universe_version_id, instrument_id, dataset_version_id = (
        _insert_durable_decision_inputs(engine, job_run.scheduled_for)
    )

    strategy = _NoTradeStrategy()
    parameters = _Parameters()
    registry = PaperJobRegistry(
        [
            PaperJobDefinition(
                job_run.job_key,
                DurableUniversePaperStrategyDecision(
                    strategy,
                    dataset_version_id,
                    job_run.scheduled_for,
                    universe_version_id,
                    parameters,
                ),
            )
        ]
    )

    outcome = run_paper_once(
        engine,
        job_run,
        registry,
        now=lambda: job_run.scheduled_for + timedelta(minutes=1),
    )

    assert outcome is PaperCycleOutcome.EXECUTED
    assert len(strategy.calls) == 1
    market_context, universe, members, received_parameters, inputs_hash = strategy.calls[0]
    assert market_context.as_of == job_run.scheduled_for
    assert len(market_context.bars) == 1
    assert market_context.bars[0].instrument_id == str(instrument_id)
    assert str(market_context.bars[0].close) == "100"
    assert market_context.bars[0].ingestion_time <= job_run.scheduled_for
    assert universe.universe_id == universe_id
    assert universe.version == "v1"
    assert universe.pit_certified is True
    assert universe.declared_member_count == 1
    assert tuple(member.instrument_id for member in members) == (instrument_id,)
    assert received_parameters is parameters
    assert len(inputs_hash) == 64


@pytest.mark.integration
def test_durable_strategy_decision_missing_universe_fails_before_lifecycle_claim() -> None:
    engine = _engine()
    job_run = create_scheduled_job_run(
        "paper-one-shot-missing-durable-universe",
        datetime(2026, 9, 10, 13, 34, tzinfo=UTC),
    )
    _prepare_job(engine, job_run)
    strategy = _NoTradeStrategy()
    registry = PaperJobRegistry(
        [
            PaperJobDefinition(
                job_run.job_key,
                DurableUniversePaperStrategyDecision(
                    strategy,
                    uuid4(),
                    job_run.scheduled_for,
                    uuid4(),
                    _Parameters(),
                ),
            )
        ]
    )

    with pytest.raises(RuntimeError, match="PAPER_DURABLE_DECISION_UNIVERSE_NOT_FOUND"):
        run_paper_once(
            engine,
            job_run,
            registry,
            now=lambda: job_run.scheduled_for + timedelta(minutes=1),
        )

    assert strategy.calls == []
    with engine.connect() as connection:
        assert SqlAlchemyJobRunRepository(connection).get_record(job_run.job_run_id) is None


@pytest.mark.integration
def test_durable_strategy_decision_missing_dataset_fails_before_lifecycle_claim() -> None:
    engine = _engine()
    job_run = create_scheduled_job_run(
        "paper-one-shot-missing-dataset",
        datetime(2026, 9, 10, 13, 35, tzinfo=UTC),
    )
    _prepare_job(engine, job_run)
    _, universe_version_id, _, _ = _insert_durable_decision_inputs(engine, job_run.scheduled_for)
    strategy = _NoTradeStrategy()
    registry = PaperJobRegistry(
        [
            PaperJobDefinition(
                job_run.job_key,
                DurableUniversePaperStrategyDecision(
                    strategy,
                    uuid4(),
                    job_run.scheduled_for,
                    universe_version_id,
                    _Parameters(),
                ),
            )
        ]
    )

    with pytest.raises(RuntimeError, match="PIT_MARKET_CONTEXT_DATASET_VERSION_NOT_FOUND"):
        run_paper_once(
            engine,
            job_run,
            registry,
            now=lambda: job_run.scheduled_for + timedelta(minutes=1),
        )

    assert strategy.calls == []
    with engine.connect() as connection:
        assert SqlAlchemyJobRunRepository(connection).get_record(job_run.job_run_id) is None
