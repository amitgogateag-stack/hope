from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.research_runs import CertifiedResearchContextLoader, CertifiedResearchRunOrchestrator, research_result_fingerprint, research_run_fingerprint, verify_research_result_evidence
from hope.infrastructure.repositories.execution_provenance import CertifiedExecutionPlanResolver, StrategyVersionRecord
from hope.infrastructure.repositories.experiments import ExperimentRecord


CONFIG = {"risk": {"max_positions": 3}, "strategy": {"lookback": 20}}


def _experiment() -> ExperimentRecord:
    return ExperimentRecord(experiment_id="exp-certified-run", hypothesis="Certified evidence only", strategy_version_id=uuid4(), dataset_version_id=uuid4(), universe_version_id=uuid4(), configuration_hash=configuration_hash(CONFIG), environment="BACKTEST", status="CREATED")


def _resolver(experiment, *, configuration=CONFIG, strategy=None):
    strategy = strategy or StrategyVersionRecord(experiment.strategy_version_id, uuid4(), "1.0.0", "abc123")
    class Provenance:
        def get_strategy_version(self, strategy_version_id): return strategy
        def get_configuration(self, configuration_hash_value): return configuration
    return CertifiedExecutionPlanResolver(Provenance())


def test_fingerprint_binds_exact_experiment_and_evidence_request() -> None:
    experiment = _experiment(); t0 = datetime(2026, 1, 2, tzinfo=timezone.utc); instruments = (uuid4(), uuid4())
    first = research_run_fingerprint(experiment, as_of=t0, instrument_ids=instruments)
    assert first == research_run_fingerprint(experiment, as_of=t0, instrument_ids=tuple(reversed(instruments)))
    assert first != research_run_fingerprint(experiment, as_of=datetime(2026, 1, 3, tzinfo=timezone.utc), instrument_ids=instruments)
    assert len(first) == 64


def test_fingerprint_rejects_duplicate_instruments() -> None:
    instrument_id = uuid4()
    with pytest.raises(ValueError, match="DUPLICATE_INSTRUMENT"):
        research_run_fingerprint(_experiment(), as_of=datetime(2026, 1, 2, tzinfo=timezone.utc), instrument_ids=(instrument_id, instrument_id))


def test_result_fingerprint_is_canonical_and_rejects_nonfinite_numbers() -> None:
    left, left_hash = research_result_fingerprint({"b": [2, 1], "a": 3}); right, right_hash = research_result_fingerprint({"a": 3, "b": [2, 1]})
    assert left == right; assert left_hash == right_hash
    with pytest.raises(ValueError): research_result_fingerprint({"bad": float("nan")})


def test_stored_result_evidence_is_independently_reconstructed() -> None:
    canonical, fingerprint = research_result_fingerprint({"count": 7, "value": 12.5})
    assert verify_research_result_evidence(canonical, fingerprint) == canonical
    with pytest.raises(ValueError, match="EVIDENCE_FINGERPRINT_MISMATCH"):
        verify_research_result_evidence({"count": 8, "value": 12.5}, fingerprint)


def test_execution_plan_resolver_binds_strategy_and_verified_configuration() -> None:
    experiment = _experiment()
    plan = _resolver(experiment).resolve(experiment)
    assert plan.experiment_id == experiment.experiment_id
    assert plan.strategy_version_id == experiment.strategy_version_id
    assert plan.configuration_hash == experiment.configuration_hash
    assert plan.configuration == CONFIG
    assert plan.code_commit == "abc123"


def test_execution_plan_resolver_rejects_missing_or_corrupt_provenance() -> None:
    experiment = _experiment()
    class MissingStrategy:
        def get_strategy_version(self, strategy_version_id): return None
        def get_configuration(self, configuration_hash_value): raise AssertionError("configuration must not be read")
    with pytest.raises(ValueError, match="STRATEGY_VERSION_MISSING"):
        CertifiedExecutionPlanResolver(MissingStrategy()).resolve(experiment)
    with pytest.raises(ValueError, match="CONFIGURATION_HASH_MISMATCH"):
        _resolver(experiment, configuration={"tampered": True}).resolve(experiment)


def test_certified_loader_derives_dataset_and_universe_from_experiment() -> None:
    experiment = _experiment(); t0 = datetime(2026, 1, 2, tzinfo=timezone.utc); instruments = (uuid4(),)
    class Experiments:
        def get(self, experiment_id): return experiment
    class Contexts:
        def get(self, dataset_version_id, *, as_of, universe_version_id, instrument_ids):
            assert dataset_version_id == experiment.dataset_version_id; assert universe_version_id == experiment.universe_version_id; return "certified-context"
    assert CertifiedResearchContextLoader(Experiments(), Contexts()).load(experiment.experiment_id, as_of=t0, instrument_ids=instruments) == "certified-context"


def test_certified_loader_rejects_unknown_experiment_before_market_read() -> None:
    class Experiments:
        def get(self, experiment_id): return None
    class Contexts:
        def get(self, *args, **kwargs): raise AssertionError("market read must not occur")
    with pytest.raises(KeyError, match="unknown experiment"):
        CertifiedResearchContextLoader(Experiments(), Contexts()).load("missing", as_of=datetime(2026, 1, 2, tzinfo=timezone.utc), instrument_ids=())


def test_orchestrator_persists_canonical_evidence_and_restart_reuses_it() -> None:
    experiment = _experiment(); t0 = datetime(2026, 1, 2, tzinfo=timezone.utc); instruments = (uuid4(),); stored = {}; executions = []
    class Experiments:
        def get(self, experiment_id): return experiment
    class Runs:
        @staticmethod
        def deterministic_id(experiment_id, fingerprint): return uuid5(NAMESPACE_URL, f"hope:research-run:{experiment_id}:{fingerprint}")
        def claim(self, run): return True
    class Contexts:
        def get(self, *args, **kwargs): return {"certified": True}
    class Evidence:
        def get(self, run_id): return stored.get(run_id)
        def persist(self, record): stored[record.research_run_id] = record; return True
    class Executor:
        def execute(self, plan, context): executions.append((plan, context)); return {"z": 2, "a": 1}
    orchestrator = CertifiedResearchRunOrchestrator(Experiments(), Runs(), Contexts(), _resolver(experiment), Executor(), Evidence())
    run, first = orchestrator.execute(experiment.experiment_id, as_of=t0, instrument_ids=instruments)
    retry_run, retry = orchestrator.execute(experiment.experiment_id, as_of=t0, instrument_ids=instruments)
    assert run == retry_run; assert first == retry == {"a": 1, "z": 2}; assert len(executions) == 1
    assert executions[0][0].strategy_version_id == experiment.strategy_version_id
    assert stored[run.research_run_id].result_fingerprint == research_result_fingerprint(first)[1]


def test_orchestrator_rejects_corrupt_stored_evidence_before_execution() -> None:
    from hope.infrastructure.repositories.research_run_evidence import ResearchRunEvidenceRecord
    experiment = _experiment(); t0 = datetime(2026, 1, 2, tzinfo=timezone.utc); instruments = (uuid4(),)
    class Experiments:
        def get(self, experiment_id): return experiment
    class Runs:
        @staticmethod
        def deterministic_id(experiment_id, fingerprint): return uuid5(NAMESPACE_URL, f"hope:research-run:{experiment_id}:{fingerprint}")
        def claim(self, run): return False
    run_id = Runs.deterministic_id(experiment.experiment_id, research_run_fingerprint(experiment, as_of=t0, instrument_ids=instruments))
    bad = ResearchRunEvidenceRecord(research_run_id=run_id, result_fingerprint="0" * 64, canonical_result={"value": 99})
    class Evidence:
        def get(self, requested_run_id): assert requested_run_id == run_id; return bad
        def persist(self, record): raise AssertionError("corrupt restart evidence must not be overwritten")
    class Contexts:
        def get(self, *args, **kwargs): raise AssertionError("corrupt restart must fail before market read")
    class Executor:
        def execute(self, plan, context): raise AssertionError("corrupt restart must not execute")
    orchestrator = CertifiedResearchRunOrchestrator(Experiments(), Runs(), Contexts(), _resolver(experiment), Executor(), Evidence())
    with pytest.raises(ValueError, match="EVIDENCE_FINGERPRINT_MISMATCH"):
        orchestrator.execute(experiment.experiment_id, as_of=t0, instrument_ids=instruments)


def test_orchestrator_rejects_unverified_execution_plan_before_claim_or_market_read() -> None:
    experiment = _experiment(); calls = []
    class Experiments:
        def get(self, experiment_id): return experiment
    class Runs:
        def deterministic_id(self, *args): calls.append("run"); raise AssertionError("must fail before claim")
    class Contexts:
        def get(self, *args, **kwargs): calls.append("market"); raise AssertionError("must fail before market read")
    class Executor:
        def execute(self, plan, context): calls.append("execute"); raise AssertionError("must fail before execution")
    orchestrator = CertifiedResearchRunOrchestrator(Experiments(), Runs(), Contexts(), _resolver(experiment, configuration={"tampered": True}), Executor())
    with pytest.raises(ValueError, match="CONFIGURATION_HASH_MISMATCH"):
        orchestrator.execute(experiment.experiment_id, as_of=datetime(2026, 1, 2, tzinfo=timezone.utc), instrument_ids=())
    assert calls == []


def test_orchestrator_never_accepts_caller_bars_or_provenance_or_executor_substitution() -> None:
    import inspect
    parameters = inspect.signature(CertifiedResearchRunOrchestrator.execute).parameters
    assert "bars" not in parameters
    assert "dataset_version_id" not in parameters
    assert "universe_version_id" not in parameters
    assert "strategy_version_id" not in parameters
    assert "configuration" not in parameters
    assert "execute" not in parameters
    assert "executor" not in parameters
