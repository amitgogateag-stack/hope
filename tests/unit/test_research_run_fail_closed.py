from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.execution import (
    CertifiedResearchExecutor,
    ResearchExecutionImplementation,
    ResearchExecutionRegistry,
)
from hope.application.experiments.research_runs import (
    CertifiedResearchReproducibilityVerifier,
    CertifiedResearchRunOrchestrator,
    research_run_fingerprint,
)
from hope.application.universe.snapshot import UniverseSnapshot
from hope.domain.universe.models import UniverseMember, UniverseVersion
from hope.infrastructure.repositories.execution_provenance import (
    CertifiedExecutionPlanResolver,
    StrategyVersionRecord,
)
from hope.infrastructure.repositories.experiments import ExperimentRecord
from hope.infrastructure.repositories.research_run_evidence import ResearchRunEvidenceRecord


CONFIG = {"strategy": {"lookback": 20}}
MANIFEST_HASH = "c" * 64


def _setup(execute):
    experiment = ExperimentRecord(
        experiment_id="exp-research-fail-closed",
        hypothesis="fail closed",
        strategy_version_id=uuid4(),
        dataset_version_id=uuid4(),
        universe_version_id=uuid4(),
        configuration_hash=configuration_hash(CONFIG),
        environment="BACKTEST",
        status="CREATED",
    )
    strategy = StrategyVersionRecord(experiment.strategy_version_id, uuid4(), "1.0.0", "abc123")

    class Provenance:
        def get_strategy_version(self, strategy_version_id):
            return strategy

        def get_configuration(self, configuration_hash_value):
            return CONFIG

    resolver = CertifiedExecutionPlanResolver(Provenance())
    implementation = ResearchExecutionImplementation(
        strategy_version_id=strategy.strategy_version_id,
        strategy_id=strategy.strategy_id,
        strategy_version=strategy.version,
        code_commit=strategy.code_commit,
        execute=execute,
    )
    executor = CertifiedResearchExecutor(ResearchExecutionRegistry([implementation]))
    return experiment, resolver, executor


def _snapshot(experiment: ExperimentRecord) -> UniverseSnapshot:
    member = UniverseMember(instrument_id=uuid4())
    return UniverseSnapshot(
        universe_version_id=experiment.universe_version_id,
        version=UniverseVersion(
            universe_id=uuid4(),
            version="v1",
            declared_member_count=1,
            pit_certified=True,
        ),
        members=(member,),
    )


def test_corrupt_restart_evidence_fails_before_market_read_or_execution() -> None:
    executions = []
    experiment, resolver, executor = _setup(lambda plan, inputs: executions.append("execute"))
    t0 = datetime(2026, 1, 2, tzinfo=timezone.utc)
    snapshot = _snapshot(experiment)
    fingerprint = research_run_fingerprint(
        experiment,
        as_of=t0,
        universe_snapshot=snapshot,
        market_data_manifest_hash=MANIFEST_HASH,
    )
    run_id = uuid5(NAMESPACE_URL, f"hope:research-run:{experiment.experiment_id}:{fingerprint}")
    bad = ResearchRunEvidenceRecord(
        research_run_id=run_id,
        result_fingerprint="0" * 64,
        canonical_result={"value": 99},
    )
    calls = []

    class Experiments:
        def get(self, experiment_id):
            return experiment

    class Runs:
        @staticmethod
        def deterministic_id(experiment_id, run_fingerprint):
            return run_id

        def claim(self, run):
            calls.append("claim")
            return False

    class Contexts:
        def manifest_hash(self, *args, **kwargs):
            calls.append("manifest")
            return MANIFEST_HASH

        def get(self, *args, **kwargs):
            calls.append("market")
            raise AssertionError("corrupt restart must fail before market read")

    class Universes:
        def get(self, universe_version_id):
            assert universe_version_id == experiment.universe_version_id
            return snapshot

    class Evidence:
        def get(self, requested_run_id):
            assert requested_run_id == run_id
            return bad

        def persist(self, record):
            raise AssertionError("corrupt restart evidence must not be overwritten")

    orchestrator = CertifiedResearchRunOrchestrator(
        Experiments(), Runs(), Contexts(), Universes(), resolver, executor, Evidence()
    )
    with pytest.raises(ValueError, match="EVIDENCE_FINGERPRINT_MISMATCH"):
        orchestrator.execute(experiment.experiment_id, as_of=t0)
    assert calls == ["manifest", "claim"]
    assert executions == []


def test_reproducibility_missing_run_fails_before_evidence_market_or_execution() -> None:
    executions = []
    experiment, resolver, executor = _setup(lambda plan, inputs: executions.append("execute"))
    snapshot = _snapshot(experiment)
    calls = []

    class Experiments:
        def get(self, experiment_id):
            return experiment

    class Runs:
        @staticmethod
        def deterministic_id(experiment_id, run_fingerprint):
            return uuid5(NAMESPACE_URL, f"hope:research-run:{experiment_id}:{run_fingerprint}")

        def get(self, run_id):
            calls.append("run")
            return None

    class Contexts:
        def manifest_hash(self, *args, **kwargs):
            calls.append("manifest")
            return MANIFEST_HASH

        def get(self, *args, **kwargs):
            calls.append("market")
            raise AssertionError("market read must not occur")

    class Universes:
        def get(self, universe_version_id):
            assert universe_version_id == experiment.universe_version_id
            return snapshot

    class Evidence:
        def get(self, run_id):
            calls.append("evidence")
            raise AssertionError("evidence read must not occur")

    verifier = CertifiedResearchReproducibilityVerifier(
        Experiments(), Runs(), Contexts(), Universes(), resolver, executor, Evidence()
    )
    with pytest.raises(ValueError, match="RESEARCH_REPRODUCIBILITY_RUN_MISSING"):
        verifier.verify(
            experiment.experiment_id,
            as_of=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
    assert calls == ["manifest", "run"]
    assert executions == []
