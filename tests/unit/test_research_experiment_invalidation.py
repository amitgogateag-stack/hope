from datetime import datetime, timezone
from uuid import uuid4

import pytest

from hope.application.experiments.execution import CertifiedResearchExecutor, ResearchExecutionRegistry
from hope.application.experiments.research_runs import (
    CertifiedResearchReproducibilityVerifier,
    CertifiedResearchRunOrchestrator,
    research_run_fingerprint,
)
from hope.application.universe.snapshot import UniverseSnapshot
from hope.domain.universe.models import UniverseVersion
from hope.infrastructure.repositories.experiments import ExperimentRecord


UTC = timezone.utc


def _invalidated_experiment() -> ExperimentRecord:
    return ExperimentRecord(
        experiment_id="exp-invalidated-research",
        hypothesis="must not replay invalidated evidence",
        strategy_version_id=uuid4(),
        dataset_version_id=uuid4(),
        universe_version_id=uuid4(),
        configuration_hash="a" * 64,
        environment="RESEARCH",
        status="CREATED",
        invalidated_at=datetime(2026, 1, 3, tzinfo=UTC),
        invalidation_reason="TEST_INVALIDATION",
    )


class _Experiments:
    def __init__(self, experiment):
        self.experiment = experiment

    def get(self, experiment_id):
        assert experiment_id == self.experiment.experiment_id
        return self.experiment


class _Untouched:
    def __getattr__(self, name):
        raise AssertionError(f"invalidated experiment touched downstream dependency: {name}")


class _Runs(_Untouched):
    @staticmethod
    def deterministic_id(*args, **kwargs):
        raise AssertionError("invalidated experiment reached run identity")


class _Universes(_Untouched):
    def get(self, *args, **kwargs):
        raise AssertionError("invalidated experiment reached universe repository")


class _Contexts(_Untouched):
    def get(self, *args, **kwargs):
        raise AssertionError("invalidated experiment reached market repository")


class _Plans(_Untouched):
    def resolve(self, *args, **kwargs):
        raise AssertionError("invalidated experiment reached execution plan resolution")


def _executor() -> CertifiedResearchExecutor:
    return CertifiedResearchExecutor(ResearchExecutionRegistry([]))


def test_run_fingerprint_rejects_invalidated_experiment() -> None:
    experiment = _invalidated_experiment()
    snapshot = UniverseSnapshot(
        universe_version_id=experiment.universe_version_id,
        version=UniverseVersion(
            universe_id=uuid4(),
            version="v1",
            pit_certified=True,
            declared_member_count=0,
        ),
        members=(),
    )
    with pytest.raises(ValueError, match="RESEARCH_EXPERIMENT_INVALIDATED"):
        research_run_fingerprint(
            experiment,
            as_of=datetime(2026, 1, 2, tzinfo=UTC),
            universe_snapshot=snapshot,
        )


def test_orchestrator_rejects_invalidated_experiment_before_restart_evidence_access() -> None:
    experiment = _invalidated_experiment()
    orchestrator = CertifiedResearchRunOrchestrator(
        _Experiments(experiment),
        _Runs(),
        _Contexts(),
        _Universes(),
        _Plans(),
        _executor(),
        evidence_repository=_Untouched(),
    )
    with pytest.raises(ValueError, match="RESEARCH_EXPERIMENT_INVALIDATED"):
        orchestrator.execute(
            experiment.experiment_id,
            as_of=datetime(2026, 1, 2, tzinfo=UTC),
        )


def test_reproducibility_rejects_invalidated_experiment_before_durable_run_access() -> None:
    experiment = _invalidated_experiment()
    verifier = CertifiedResearchReproducibilityVerifier(
        _Experiments(experiment),
        _Runs(),
        _Contexts(),
        _Universes(),
        _Plans(),
        _executor(),
        _Untouched(),
    )
    with pytest.raises(ValueError, match="RESEARCH_EXPERIMENT_INVALIDATED"):
        verifier.verify(
            experiment.experiment_id,
            as_of=datetime(2026, 1, 2, tzinfo=UTC),
        )
