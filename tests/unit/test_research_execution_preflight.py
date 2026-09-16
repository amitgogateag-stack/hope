from datetime import datetime, timezone
from uuid import uuid4

import pytest

from hope.application.experiments.execution import CertifiedResearchExecutor, ResearchExecutionRegistry
from hope.application.experiments.research_runs import (
    CertifiedResearchReproducibilityVerifier,
    CertifiedResearchRunOrchestrator,
)
from hope.infrastructure.repositories.execution_provenance import CertifiedExecutionPlan
from hope.infrastructure.repositories.experiments import ExperimentRecord


UTC = timezone.utc


def _experiment() -> ExperimentRecord:
    return ExperimentRecord(
        experiment_id="exp-preflight",
        hypothesis="reject missing executable before research side effects",
        strategy_version_id=uuid4(),
        dataset_version_id=uuid4(),
        universe_version_id=uuid4(),
        configuration_hash="a" * 64,
        environment="BACKTEST",
        status="CREATED",
    )


def _plan(experiment: ExperimentRecord) -> CertifiedExecutionPlan:
    return CertifiedExecutionPlan(
        experiment_id=experiment.experiment_id,
        strategy_version_id=experiment.strategy_version_id,
        strategy_id=uuid4(),
        strategy_version="1.0.0",
        code_commit="abc123",
        configuration_hash=experiment.configuration_hash,
        configuration={"strategy": {"lookback": 20}},
    )


class _Experiments:
    def __init__(self, experiment):
        self._experiment = experiment

    def get(self, experiment_id):
        assert experiment_id == self._experiment.experiment_id
        return self._experiment


class _Plans:
    def __init__(self, plan):
        self._plan = plan

    def resolve(self, experiment):
        assert experiment.experiment_id == self._plan.experiment_id
        return self._plan


class _Untouched:
    def __getattr__(self, name):
        raise AssertionError(f"registry preflight must fail before downstream dependency: {name}")


class _Universes(_Untouched):
    def get(self, *args, **kwargs):
        raise AssertionError("registry preflight must fail before universe access")


class _Contexts(_Untouched):
    def get(self, *args, **kwargs):
        raise AssertionError("registry preflight must fail before market read")


class _Runs(_Untouched):
    @staticmethod
    def deterministic_id(*args, **kwargs):
        raise AssertionError("registry preflight must fail before run identity")

    def claim(self, *args, **kwargs):
        raise AssertionError("registry preflight must fail before run claim")

    def get(self, *args, **kwargs):
        raise AssertionError("registry preflight must fail before run read")


class _Evidence(_Untouched):
    def get(self, *args, **kwargs):
        raise AssertionError("registry preflight must fail before evidence access")


def _unregistered_executor() -> CertifiedResearchExecutor:
    return CertifiedResearchExecutor(ResearchExecutionRegistry([]))


def test_orchestrator_preflights_registry_before_universe_run_or_market_access() -> None:
    experiment = _experiment()
    orchestrator = CertifiedResearchRunOrchestrator(
        _Experiments(experiment),
        _Runs(),
        _Contexts(),
        _Universes(),
        _Plans(_plan(experiment)),
        _unregistered_executor(),
        _Evidence(),
    )

    with pytest.raises(RuntimeError, match="RESEARCH_IMPLEMENTATION_NOT_REGISTERED"):
        orchestrator.execute(
            experiment.experiment_id,
            as_of=datetime(2026, 1, 2, tzinfo=UTC),
        )


def test_reproducibility_preflights_registry_before_durable_or_market_access() -> None:
    experiment = _experiment()
    verifier = CertifiedResearchReproducibilityVerifier(
        _Experiments(experiment),
        _Runs(),
        _Contexts(),
        _Universes(),
        _Plans(_plan(experiment)),
        _unregistered_executor(),
        _Evidence(),
    )

    with pytest.raises(RuntimeError, match="RESEARCH_IMPLEMENTATION_NOT_REGISTERED"):
        verifier.verify(
            experiment.experiment_id,
            as_of=datetime(2026, 1, 2, tzinfo=UTC),
        )


def test_executor_validate_rejects_non_plan_before_registry_lookup() -> None:
    with pytest.raises(TypeError, match="CERTIFIED_RESEARCH_EXECUTOR_REQUIRES_EXECUTION_PLAN"):
        _unregistered_executor().validate(object())
