from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5

import inspect
import pytest

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.execution import (
    CertifiedResearchExecutor,
    ResearchExecutionImplementation,
    ResearchExecutionRegistry,
)
from hope.application.experiments.research_runs import (
    CertifiedResearchContextLoader,
    CertifiedResearchReproducibilityVerifier,
    CertifiedResearchRunOrchestrator,
    research_result_fingerprint,
    research_run_fingerprint,
    verify_research_result_evidence,
)
from hope.infrastructure.repositories.execution_provenance import (
    CertifiedExecutionPlanResolver,
    StrategyVersionRecord,
)
from hope.infrastructure.repositories.experiments import ExperimentRecord


CONFIG = {"risk": {"max_positions": 3}, "strategy": {"lookback": 20}}


def _experiment() -> ExperimentRecord:
    return ExperimentRecord(
        experiment_id="exp-certified-run",
        hypothesis="Certified evidence only",
        strategy_version_id=uuid4(),
        dataset_version_id=uuid4(),
        universe_version_id=uuid4(),
        configuration_hash=configuration_hash(CONFIG),
        environment="BACKTEST",
        status="CREATED",
    )


def _strategy_record(experiment: ExperimentRecord) -> StrategyVersionRecord:
    return StrategyVersionRecord(experiment.strategy_version_id, uuid4(), "1.0.0", "abc123")


def _resolver(experiment, *, configuration=CONFIG, strategy=None):
    strategy = strategy or _strategy_record(experiment)

    class Provenance:
        def get_strategy_version(self, strategy_version_id):
            return strategy

        def get_configuration(self, configuration_hash_value):
            return configuration

    return CertifiedExecutionPlanResolver(Provenance())


def _certified_executor(experiment, execute, *, strategy=None):
    strategy = strategy or _strategy_record(experiment)
    implementation = ResearchExecutionImplementation(
        strategy_version_id=strategy.strategy_version_id,
        strategy_id=strategy.strategy_id,
        strategy_version=strategy.version,
        code_commit=strategy.code_commit,
        execute=execute,
    )
    return CertifiedResearchExecutor(ResearchExecutionRegistry([implementation])), strategy


def test_fingerprint_binds_exact_experiment_and_evidence_request() -> None:
    experiment = _experiment()
    t0 = datetime(2026, 1, 2, tzinfo=timezone.utc)
    instruments = (uuid4(), uuid4())
    first = research_run_fingerprint(experiment, as_of=t0, instrument_ids=instruments)
    assert first == research_run_fingerprint(
        experiment, as_of=t0, instrument_ids=tuple(reversed(instruments))
    )
    assert first != research_run_fingerprint(
        experiment,
        as_of=datetime(2026, 1, 3, tzinfo=timezone.utc),
        instrument_ids=instruments,
    )
    assert len(first) == 64


def test_fingerprint_rejects_duplicate_instruments() -> None:
    instrument_id = uuid4()
    with pytest.raises(ValueError, match="DUPLICATE_INSTRUMENT"):
        research_run_fingerprint(
            _experiment(),
            as_of=datetime(2026, 1, 2, tzinfo=timezone.utc),
            instrument_ids=(instrument_id, instrument_id),
        )


def test_result_fingerprint_is_canonical_and_stored_evidence_is_verified() -> None:
    left, left_hash = research_result_fingerprint({"b": [2, 1], "a": 3})
    right, right_hash = research_result_fingerprint({"a": 3, "b": [2, 1]})
    assert left == right
    assert left_hash == right_hash
    assert verify_research_result_evidence(left, left_hash) == left
    with pytest.raises(ValueError, match="EVIDENCE_FINGERPRINT_MISMATCH"):
        verify_research_result_evidence({"a": 4, "b": [2, 1]}, left_hash)
    with pytest.raises(ValueError):
        research_result_fingerprint({"bad": float("nan")})


def test_execution_plan_resolver_binds_strategy_and_verified_configuration() -> None:
    experiment = _experiment()
    strategy = _strategy_record(experiment)
    plan = _resolver(experiment, strategy=strategy).resolve(experiment)
    assert plan.experiment_id == experiment.experiment_id
    assert plan.strategy_version_id == experiment.strategy_version_id
    assert plan.strategy_id == strategy.strategy_id
    assert plan.configuration_hash == experiment.configuration_hash
    assert plan.configuration == CONFIG
    assert plan.code_commit == "abc123"


def test_execution_plan_resolver_rejects_missing_or_corrupt_provenance() -> None:
    experiment = _experiment()

    class MissingStrategy:
        def get_strategy_version(self, strategy_version_id):
            return None

        def get_configuration(self, configuration_hash_value):
            raise AssertionError("configuration must not be read")

    with pytest.raises(ValueError, match="STRATEGY_VERSION_MISSING"):
        CertifiedExecutionPlanResolver(MissingStrategy()).resolve(experiment)
    with pytest.raises(ValueError, match="CONFIGURATION_HASH_MISMATCH"):
        _resolver(experiment, configuration={"tampered": True}).resolve(experiment)


def test_certified_loader_derives_dataset_and_universe_from_experiment() -> None:
    experiment = _experiment()
    t0 = datetime(2026, 1, 2, tzinfo=timezone.utc)
    instruments = (uuid4(),)

    class Experiments:
        def get(self, experiment_id):
            return experiment

    class Contexts:
        def get(self, dataset_version_id, *, as_of, universe_version_id, instrument_ids):
            assert dataset_version_id == experiment.dataset_version_id
            assert universe_version_id == experiment.universe_version_id
            assert instrument_ids == instruments
            return "certified-context"

    loader = CertifiedResearchContextLoader(Experiments(), Contexts())
    assert loader.load(experiment.experiment_id, as_of=t0, instrument_ids=instruments) == "certified-context"


def test_orchestrator_rejects_arbitrary_executor_at_composition_boundary() -> None:
    experiment = _experiment()

    class ArbitraryExecutor:
        def execute(self, plan, context):
            return {"unsafe": True}

    with pytest.raises(TypeError, match="RESEARCH_RUN_REQUIRES_CERTIFIED_EXECUTOR"):
        CertifiedResearchRunOrchestrator(
            object(), object(), object(), _resolver(experiment), ArbitraryExecutor()
        )

    with pytest.raises(TypeError, match="RESEARCH_RUN_REQUIRES_CERTIFIED_EXECUTOR"):
        CertifiedResearchReproducibilityVerifier(
            object(), object(), object(), _resolver(experiment), ArbitraryExecutor(), object()
        )


def test_orchestrator_persists_canonical_evidence_and_restart_reuses_it() -> None:
    experiment = _experiment()
    strategy = _strategy_record(experiment)
    t0 = datetime(2026, 1, 2, tzinfo=timezone.utc)
    instruments = (uuid4(),)
    stored = {}
    executions = []

    class Experiments:
        def get(self, experiment_id):
            return experiment

    class Runs:
        @staticmethod
        def deterministic_id(experiment_id, fingerprint):
            return uuid5(NAMESPACE_URL, f"hope:research-run:{experiment_id}:{fingerprint}")

        def claim(self, run):
            return True

    class Contexts:
        def get(self, *args, **kwargs):
            return {"certified": True}

    class Evidence:
        def get(self, run_id):
            return stored.get(run_id)

        def persist(self, record):
            stored[record.research_run_id] = record
            return True

    def execute(plan, context):
        executions.append((plan, context))
        return {"z": 2, "a": 1}

    certified_executor, _ = _certified_executor(experiment, execute, strategy=strategy)
    orchestrator = CertifiedResearchRunOrchestrator(
        Experiments(),
        Runs(),
        Contexts(),
        _resolver(experiment, strategy=strategy),
        certified_executor,
        Evidence(),
    )
    run, first = orchestrator.execute(experiment.experiment_id, as_of=t0, instrument_ids=instruments)
    retry_run, retry = orchestrator.execute(
        experiment.experiment_id, as_of=t0, instrument_ids=instruments
    )
    assert run == retry_run
    assert first == retry == {"a": 1, "z": 2}
    assert len(executions) == 1
    assert executions[0][0].strategy_version_id == experiment.strategy_version_id
    assert stored[run.research_run_id].result_fingerprint == research_result_fingerprint(first)[1]


def test_orchestrator_fails_before_claim_when_provenance_is_invalid() -> None:
    experiment = _experiment()
    strategy = _strategy_record(experiment)
    calls = []

    class Experiments:
        def get(self, experiment_id):
            return experiment

    class Runs:
        def deterministic_id(self, *args):
            calls.append("run")
            raise AssertionError("must fail before claim")

    class Contexts:
        def get(self, *args, **kwargs):
            calls.append("market")
            raise AssertionError("must fail before market read")

    def execute(plan, context):
        calls.append("execute")
        raise AssertionError("must fail before execution")

    certified_executor, _ = _certified_executor(experiment, execute, strategy=strategy)
    orchestrator = CertifiedResearchRunOrchestrator(
        Experiments(),
        Runs(),
        Contexts(),
        _resolver(experiment, configuration={"tampered": True}, strategy=strategy),
        certified_executor,
    )
    with pytest.raises(ValueError, match="CONFIGURATION_HASH_MISMATCH"):
        orchestrator.execute(
            experiment.experiment_id,
            as_of=datetime(2026, 1, 2, tzinfo=timezone.utc),
            instrument_ids=(),
        )
    assert calls == []


def test_orchestrator_fails_closed_when_registry_identity_does_not_match_plan() -> None:
    experiment = _experiment()
    strategy = _strategy_record(experiment)
    t0 = datetime(2026, 1, 2, tzinfo=timezone.utc)
    instruments = (uuid4(),)
    calls = []

    class Experiments:
        def get(self, experiment_id):
            return experiment

    class Runs:
        @staticmethod
        def deterministic_id(experiment_id, fingerprint):
            return uuid5(NAMESPACE_URL, f"hope:research-run:{experiment_id}:{fingerprint}")

        def claim(self, run):
            calls.append("claim")
            return True

    class Contexts:
        def get(self, *args, **kwargs):
            calls.append("market")
            return {"certified": True}

    wrong = ResearchExecutionImplementation(
        strategy_version_id=strategy.strategy_version_id,
        strategy_id=uuid4(),
        strategy_version=strategy.version,
        code_commit=strategy.code_commit,
        execute=lambda plan, context: calls.append("execute"),
    )
    executor = CertifiedResearchExecutor(ResearchExecutionRegistry([wrong]))
    orchestrator = CertifiedResearchRunOrchestrator(
        Experiments(), Runs(), Contexts(), _resolver(experiment, strategy=strategy), executor
    )
    with pytest.raises(ValueError, match="RESEARCH_IMPLEMENTATION_STRATEGY_ID_MISMATCH"):
        orchestrator.execute(experiment.experiment_id, as_of=t0, instrument_ids=instruments)
    assert calls == ["claim", "market"]


def test_reproducibility_verifier_reexecutes_same_registered_implementation() -> None:
    from hope.infrastructure.repositories.research_run_evidence import ResearchRunEvidenceRecord
    from hope.infrastructure.repositories.research_runs import ResearchRunRecord

    experiment = _experiment()
    strategy = _strategy_record(experiment)
    t0 = datetime(2026, 1, 2, tzinfo=timezone.utc)
    instruments = (uuid4(),)
    fingerprint = research_run_fingerprint(experiment, as_of=t0, instrument_ids=instruments)
    run_id = uuid5(NAMESPACE_URL, f"hope:research-run:{experiment.experiment_id}:{fingerprint}")
    run = ResearchRunRecord(
        research_run_id=run_id,
        experiment_id=experiment.experiment_id,
        run_fingerprint=fingerprint,
        as_of=t0,
    )
    canonical, result_hash = research_result_fingerprint({"net": 12, "trades": 3})
    evidence = ResearchRunEvidenceRecord(
        research_run_id=run_id,
        result_fingerprint=result_hash,
        canonical_result=canonical,
    )
    calls = []

    class Experiments:
        def get(self, experiment_id):
            return experiment

    class Runs:
        @staticmethod
        def deterministic_id(experiment_id, run_fingerprint):
            return uuid5(NAMESPACE_URL, f"hope:research-run:{experiment_id}:{run_fingerprint}")

        def get(self, requested_run_id):
            assert requested_run_id == run_id
            return run

    class Contexts:
        def get(self, dataset_version_id, *, as_of, universe_version_id, instrument_ids):
            assert dataset_version_id == experiment.dataset_version_id
            assert universe_version_id == experiment.universe_version_id
            calls.append("market")
            return {"certified": True}

    class Evidence:
        def get(self, requested_run_id):
            assert requested_run_id == run_id
            return evidence

    def execute(plan, context):
        calls.append((plan.strategy_version_id, context))
        return {"trades": 3, "net": 12}

    certified_executor, _ = _certified_executor(experiment, execute, strategy=strategy)
    verifier = CertifiedResearchReproducibilityVerifier(
        Experiments(),
        Runs(),
        Contexts(),
        _resolver(experiment, strategy=strategy),
        certified_executor,
        Evidence(),
    )
    assert verifier.verify(experiment.experiment_id, as_of=t0, instrument_ids=instruments) == canonical
    assert calls[0] == "market"
    assert calls[1][0] == experiment.strategy_version_id


def test_reproducibility_verifier_fails_closed_on_divergent_reexecution() -> None:
    from hope.infrastructure.repositories.research_run_evidence import ResearchRunEvidenceRecord
    from hope.infrastructure.repositories.research_runs import ResearchRunRecord

    experiment = _experiment()
    strategy = _strategy_record(experiment)
    t0 = datetime(2026, 1, 2, tzinfo=timezone.utc)
    instruments = (uuid4(),)
    fingerprint = research_run_fingerprint(experiment, as_of=t0, instrument_ids=instruments)
    run_id = uuid5(NAMESPACE_URL, f"hope:research-run:{experiment.experiment_id}:{fingerprint}")
    run = ResearchRunRecord(
        research_run_id=run_id,
        experiment_id=experiment.experiment_id,
        run_fingerprint=fingerprint,
        as_of=t0,
    )
    canonical, result_hash = research_result_fingerprint({"net": 12})
    evidence = ResearchRunEvidenceRecord(
        research_run_id=run_id,
        result_fingerprint=result_hash,
        canonical_result=canonical,
    )

    class Experiments:
        def get(self, experiment_id):
            return experiment

    class Runs:
        @staticmethod
        def deterministic_id(experiment_id, run_fingerprint):
            return run_id

        def get(self, requested_run_id):
            return run

    class Contexts:
        def get(self, *args, **kwargs):
            return {"certified": True}

    class Evidence:
        def get(self, requested_run_id):
            return evidence

    certified_executor, _ = _certified_executor(
        experiment, lambda plan, context: {"net": 11}, strategy=strategy
    )
    verifier = CertifiedResearchReproducibilityVerifier(
        Experiments(),
        Runs(),
        Contexts(),
        _resolver(experiment, strategy=strategy),
        certified_executor,
        Evidence(),
    )
    with pytest.raises(ValueError, match="RESEARCH_REPRODUCIBILITY_MISMATCH"):
        verifier.verify(experiment.experiment_id, as_of=t0, instrument_ids=instruments)


def test_public_run_api_never_accepts_caller_bars_or_provenance_or_executor_substitution() -> None:
    parameters = inspect.signature(CertifiedResearchRunOrchestrator.execute).parameters
    for forbidden in (
        "bars",
        "dataset_version_id",
        "universe_version_id",
        "strategy_version_id",
        "configuration",
        "execute",
        "executor",
        "certified_executor",
    ):
        assert forbidden not in parameters

    init_parameters = inspect.signature(CertifiedResearchRunOrchestrator.__init__).parameters
    assert "executor" not in init_parameters
    assert "certified_executor" in init_parameters
