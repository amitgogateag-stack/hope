from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.execution import (
    CertifiedResearchExecutor,
    CertifiedResearchInputs,
    ResearchExecutionImplementation,
    ResearchExecutionRegistry,
)
from hope.application.experiments.research_runs import (
    CertifiedResearchInputLoader,
    CertifiedResearchReproducibilityVerifier,
    CertifiedResearchRunOrchestrator,
    research_result_fingerprint,
    research_run_fingerprint,
    verify_research_result_evidence,
)
from hope.application.universe.snapshot import UniverseSnapshot
from hope.domain.market_data.context import PITMarketContext
from hope.domain.universe.models import UniverseMember, UniverseVersion
from hope.infrastructure.repositories.execution_provenance import CertifiedExecutionPlanResolver, StrategyVersionRecord
from hope.infrastructure.repositories.experiments import ExperimentRecord


UTC = timezone.utc
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


def _snapshot(experiment: ExperimentRecord, instrument_ids=None) -> UniverseSnapshot:
    ids = instrument_ids or (uuid4(), uuid4())
    return UniverseSnapshot(
        universe_version_id=experiment.universe_version_id,
        version=UniverseVersion(
            universe_id=uuid4(),
            version="v1",
            declared_member_count=len(ids),
            pit_certified=True,
        ),
        members=tuple(UniverseMember(instrument_id=value) for value in ids),
    )


def _resolver(experiment, *, configuration=CONFIG, strategy=None):
    strategy = strategy or StrategyVersionRecord(
        experiment.strategy_version_id,
        uuid4(),
        "1.0.0",
        "abc123",
    )

    class Provenance:
        def get_strategy_version(self, strategy_version_id):
            return strategy

        def get_configuration(self, configuration_hash_value):
            return configuration

    return CertifiedExecutionPlanResolver(Provenance()), strategy


def _executor(experiment, strategy, handler):
    implementation = ResearchExecutionImplementation(
        strategy_version_id=experiment.strategy_version_id,
        strategy_id=strategy.strategy_id,
        strategy_version=strategy.version,
        code_commit=strategy.code_commit,
        execute=handler,
    )
    return CertifiedResearchExecutor(ResearchExecutionRegistry([implementation]))


def test_fingerprint_binds_exact_experiment_as_of_and_universe_membership() -> None:
    experiment = _experiment()
    t0 = datetime(2026, 1, 2, tzinfo=UTC)
    snapshot = _snapshot(experiment)
    first = research_run_fingerprint(experiment, as_of=t0, universe_snapshot=snapshot)
    assert first == research_run_fingerprint(experiment, as_of=t0, universe_snapshot=snapshot)
    assert first != research_run_fingerprint(
        experiment,
        as_of=datetime(2026, 1, 3, tzinfo=UTC),
        universe_snapshot=snapshot,
    )
    changed = _snapshot(experiment, instrument_ids=(uuid4(),))
    assert first != research_run_fingerprint(experiment, as_of=t0, universe_snapshot=changed)
    assert len(first) == 64


def test_fingerprint_rejects_wrong_universe() -> None:
    experiment = _experiment()
    wrong_experiment = _experiment()
    with pytest.raises(ValueError, match="UNIVERSE_VERSION_MISMATCH"):
        research_run_fingerprint(
            experiment,
            as_of=datetime(2026, 1, 2, tzinfo=UTC),
            universe_snapshot=_snapshot(wrong_experiment),
        )


def test_result_fingerprint_is_canonical_and_rejects_nonfinite_numbers() -> None:
    left, left_hash = research_result_fingerprint({"b": [2, 1], "a": 3})
    right, right_hash = research_result_fingerprint({"a": 3, "b": [2, 1]})
    assert left == right
    assert left_hash == right_hash
    with pytest.raises(ValueError):
        research_result_fingerprint({"bad": float("nan")})


def test_stored_result_evidence_is_independently_reconstructed() -> None:
    canonical, fingerprint = research_result_fingerprint({"count": 7, "value": 12.5})
    assert verify_research_result_evidence(canonical, fingerprint) == canonical
    with pytest.raises(ValueError, match="EVIDENCE_FINGERPRINT_MISMATCH"):
        verify_research_result_evidence({"count": 8, "value": 12.5}, fingerprint)


def test_input_loader_derives_exact_instruments_from_frozen_universe() -> None:
    experiment = _experiment()
    snapshot = _snapshot(experiment)
    t0 = datetime(2026, 1, 2, tzinfo=UTC)
    calls = []

    class Universes:
        def get(self, universe_version_id):
            assert universe_version_id == experiment.universe_version_id
            return snapshot

    class Contexts:
        def get(self, dataset_version_id, *, as_of, universe_version_id, instrument_ids):
            calls.append((dataset_version_id, as_of, universe_version_id, instrument_ids))
            return PITMarketContext(as_of=as_of, bars=())

    loader = CertifiedResearchInputLoader(Contexts(), Universes())
    resolved = loader.universe(experiment)
    inputs = loader.load(experiment, as_of=t0, universe_snapshot=resolved)
    assert isinstance(inputs, CertifiedResearchInputs)
    assert inputs.universe_snapshot == snapshot
    assert calls == [(
        experiment.dataset_version_id,
        t0,
        experiment.universe_version_id,
        tuple(member.instrument_id for member in snapshot.members),
    )]


def test_input_loader_rejects_missing_universe_before_market_read() -> None:
    experiment = _experiment()

    class Universes:
        def get(self, universe_version_id):
            return None

    class Contexts:
        def get(self, *args, **kwargs):
            raise AssertionError("market read must not occur")

    loader = CertifiedResearchInputLoader(Contexts(), Universes())
    with pytest.raises(ValueError, match="UNIVERSE_SNAPSHOT_MISSING"):
        loader.universe(experiment)


def test_orchestrator_persists_evidence_and_restart_reuses_without_market_read() -> None:
    experiment = _experiment()
    snapshot = _snapshot(experiment)
    t0 = datetime(2026, 1, 2, tzinfo=UTC)
    stored = {}
    market_reads = []
    executions = []
    resolver, strategy = _resolver(experiment)

    class Experiments:
        def get(self, experiment_id):
            return experiment

    class Universes:
        def get(self, universe_version_id):
            return snapshot

    class Runs:
        @staticmethod
        def deterministic_id(experiment_id, fingerprint):
            return uuid5(NAMESPACE_URL, f"hope:research-run:{experiment_id}:{fingerprint}")

        def claim(self, run):
            return True

    class Contexts:
        def get(self, dataset_version_id, *, as_of, universe_version_id, instrument_ids):
            market_reads.append(instrument_ids)
            return PITMarketContext(as_of=as_of, bars=())

    class Evidence:
        def get(self, run_id):
            return stored.get(run_id)

        def persist(self, record):
            stored[record.research_run_id] = record
            return True

    def handler(plan, inputs):
        executions.append((plan, inputs))
        return {"z": 2, "a": 1}

    orchestrator = CertifiedResearchRunOrchestrator(
        Experiments(),
        Runs(),
        Contexts(),
        Universes(),
        resolver,
        _executor(experiment, strategy, handler),
        Evidence(),
    )
    run, first = orchestrator.execute(experiment.experiment_id, as_of=t0)
    retry_run, retry = orchestrator.execute(experiment.experiment_id, as_of=t0)
    assert run == retry_run
    assert first == retry == {"a": 1, "z": 2}
    assert len(executions) == 1
    assert len(market_reads) == 1
    assert market_reads[0] == tuple(member.instrument_id for member in snapshot.members)
    assert executions[0][1].universe_snapshot == snapshot


def test_orchestrator_rejects_corrupt_stored_evidence_before_market_read() -> None:
    from hope.infrastructure.repositories.research_run_evidence import ResearchRunEvidenceRecord

    experiment = _experiment()
    snapshot = _snapshot(experiment)
    t0 = datetime(2026, 1, 2, tzinfo=UTC)
    resolver, strategy = _resolver(experiment)
    fingerprint = research_run_fingerprint(experiment, as_of=t0, universe_snapshot=snapshot)
    run_id = uuid5(NAMESPACE_URL, f"hope:research-run:{experiment.experiment_id}:{fingerprint}")
    bad = ResearchRunEvidenceRecord(
        research_run_id=run_id,
        result_fingerprint="0" * 64,
        canonical_result={"value": 99},
    )

    class Experiments:
        def get(self, experiment_id): return experiment

    class Universes:
        def get(self, universe_version_id): return snapshot

    class Runs:
        @staticmethod
        def deterministic_id(experiment_id, run_fingerprint): return run_id
        def claim(self, run): return False

    class Evidence:
        def get(self, requested_run_id): return bad
        def persist(self, record): raise AssertionError("must not overwrite corrupt evidence")

    class Contexts:
        def get(self, *args, **kwargs): raise AssertionError("must fail before market read")

    executor = _executor(
        experiment,
        strategy,
        lambda plan, inputs: (_ for _ in ()).throw(AssertionError("must not execute")),
    )
    orchestrator = CertifiedResearchRunOrchestrator(
        Experiments(), Runs(), Contexts(), Universes(), resolver, executor, Evidence()
    )
    with pytest.raises(ValueError, match="EVIDENCE_FINGERPRINT_MISMATCH"):
        orchestrator.execute(experiment.experiment_id, as_of=t0)


def test_reproducibility_verifier_reexecutes_same_frozen_universe_and_matches() -> None:
    from hope.infrastructure.repositories.research_run_evidence import ResearchRunEvidenceRecord
    from hope.infrastructure.repositories.research_runs import ResearchRunRecord

    experiment = _experiment()
    snapshot = _snapshot(experiment)
    t0 = datetime(2026, 1, 2, tzinfo=UTC)
    fingerprint = research_run_fingerprint(experiment, as_of=t0, universe_snapshot=snapshot)
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
    resolver, strategy = _resolver(experiment)
    seen = []

    class Experiments:
        def get(self, experiment_id): return experiment

    class Universes:
        def get(self, universe_version_id): return snapshot

    class Runs:
        @staticmethod
        def deterministic_id(experiment_id, run_fingerprint): return run_id
        def get(self, requested_run_id): return run

    class Contexts:
        def get(self, dataset_version_id, *, as_of, universe_version_id, instrument_ids):
            seen.append(instrument_ids)
            return PITMarketContext(as_of=as_of, bars=())

    class Evidence:
        def get(self, requested_run_id): return evidence

    executor = _executor(experiment, strategy, lambda plan, inputs: {"trades": 3, "net": 12})
    verifier = CertifiedResearchReproducibilityVerifier(
        Experiments(), Runs(), Contexts(), Universes(), resolver, executor, Evidence()
    )
    assert verifier.verify(experiment.experiment_id, as_of=t0) == canonical
    assert seen == [tuple(member.instrument_id for member in snapshot.members)]


def test_reproducibility_verifier_fails_closed_on_divergent_reexecution() -> None:
    from hope.infrastructure.repositories.research_run_evidence import ResearchRunEvidenceRecord
    from hope.infrastructure.repositories.research_runs import ResearchRunRecord

    experiment = _experiment()
    snapshot = _snapshot(experiment)
    t0 = datetime(2026, 1, 2, tzinfo=UTC)
    fingerprint = research_run_fingerprint(experiment, as_of=t0, universe_snapshot=snapshot)
    run_id = uuid5(NAMESPACE_URL, f"hope:research-run:{experiment.experiment_id}:{fingerprint}")
    run = ResearchRunRecord(research_run_id=run_id, experiment_id=experiment.experiment_id, run_fingerprint=fingerprint, as_of=t0)
    canonical, result_hash = research_result_fingerprint({"net": 12})
    evidence = ResearchRunEvidenceRecord(research_run_id=run_id, result_fingerprint=result_hash, canonical_result=canonical)
    resolver, strategy = _resolver(experiment)

    class Experiments:
        def get(self, experiment_id): return experiment
    class Universes:
        def get(self, universe_version_id): return snapshot
    class Runs:
        @staticmethod
        def deterministic_id(experiment_id, run_fingerprint): return run_id
        def get(self, requested_run_id): return run
    class Contexts:
        def get(self, *args, **kwargs): return PITMarketContext(as_of=t0, bars=())
    class Evidence:
        def get(self, requested_run_id): return evidence

    verifier = CertifiedResearchReproducibilityVerifier(
        Experiments(),
        Runs(),
        Contexts(),
        Universes(),
        resolver,
        _executor(experiment, strategy, lambda plan, inputs: {"net": 11}),
        Evidence(),
    )
    with pytest.raises(ValueError, match="RESEARCH_REPRODUCIBILITY_MISMATCH"):
        verifier.verify(experiment.experiment_id, as_of=t0)


def test_research_public_apis_do_not_accept_instrument_or_executor_substitution() -> None:
    import inspect

    execute_parameters = inspect.signature(CertifiedResearchRunOrchestrator.execute).parameters
    verify_parameters = inspect.signature(CertifiedResearchReproducibilityVerifier.verify).parameters
    for parameters in (execute_parameters, verify_parameters):
        assert "instrument_ids" not in parameters
        assert "bars" not in parameters
        assert "dataset_version_id" not in parameters
        assert "universe_version_id" not in parameters
        assert "strategy_version_id" not in parameters
        assert "configuration" not in parameters
        assert "execute" not in parameters
        assert "executor" not in parameters


def test_orchestrator_and_verifier_require_certified_executor() -> None:
    experiment = _experiment()
    snapshot = _snapshot(experiment)
    resolver, _ = _resolver(experiment)

    class Experiments:
        def get(self, experiment_id): return experiment
    class Universes:
        def get(self, universe_version_id): return snapshot

    args = (Experiments(), object(), object(), Universes(), resolver)
    with pytest.raises(TypeError, match="REQUIRES_CERTIFIED_EXECUTOR"):
        CertifiedResearchRunOrchestrator(*args, object())
    with pytest.raises(TypeError, match="REQUIRES_CERTIFIED_EXECUTOR"):
        CertifiedResearchReproducibilityVerifier(*args, object(), object())
