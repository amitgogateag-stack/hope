from datetime import datetime, timezone
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

from hope.application.backtests.analytics import BacktestMetrics
from hope.application.backtests.engine import BacktestResult
from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.execution import (
    CertifiedResearchExecutor,
    ResearchExecutionImplementation,
    ResearchExecutionRegistry,
)
from hope.application.experiments.research_runs import (
    CertifiedResearchRunOrchestrator,
    research_result_fingerprint,
    research_run_fingerprint,
)
from hope.application.universe.snapshot import UniverseSnapshot
from hope.domain.portfolio.ledger import PortfolioState
from hope.domain.universe.models import UniverseMember, UniverseVersion
from hope.infrastructure.repositories.execution_provenance import (
    CertifiedExecutionPlan,
    CertifiedExecutionPlanResolver,
    StrategyVersionRecord,
)
from hope.infrastructure.repositories.experiments import ExperimentRecord
from hope.infrastructure.repositories.research_run_evidence import ResearchRunEvidenceRecord


UTC = timezone.utc
CONFIG = {"execution": {"latency_ms": 0}, "strategy": {"lookback": 20}}


def _result() -> BacktestResult:
    metrics = BacktestMetrics(
        Decimal("100"),
        Decimal("100"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
    )
    return BacktestResult(
        initial_cash=Decimal("100"),
        final_state=PortfolioState(cash=Decimal("100"), positions={}),
        events=(),
        valuations=(),
        metrics=metrics,
    )


def test_orchestrator_rejects_stored_certified_backtest_from_different_execution_plan() -> None:
    strategy_version_id = uuid4()
    strategy_id = uuid4()
    dataset_version_id = uuid4()
    universe_version_id = uuid4()
    experiment = ExperimentRecord(
        experiment_id="exp-certified-restart",
        hypothesis="Restart provenance must match",
        strategy_version_id=strategy_version_id,
        dataset_version_id=dataset_version_id,
        universe_version_id=universe_version_id,
        configuration_hash=configuration_hash(CONFIG),
        environment="BACKTEST",
        status="CREATED",
    )
    current_strategy = StrategyVersionRecord(
        strategy_version_id,
        strategy_id,
        "1.0.0",
        "current-commit",
    )

    class Provenance:
        def get_strategy_version(self, requested_id):
            assert requested_id == strategy_version_id
            return current_strategy

        def get_configuration(self, requested_hash):
            assert requested_hash == experiment.configuration_hash
            return CONFIG

    resolver = CertifiedExecutionPlanResolver(Provenance())
    current_plan = resolver.resolve(experiment)
    stale_plan = CertifiedExecutionPlan(
        experiment_id=current_plan.experiment_id,
        strategy_version_id=current_plan.strategy_version_id,
        strategy_id=current_plan.strategy_id,
        strategy_version=current_plan.strategy_version,
        code_commit="stale-commit",
        configuration_hash=current_plan.configuration_hash,
        configuration=current_plan.configuration,
    )

    instrument_id = uuid4()
    snapshot = UniverseSnapshot(
        universe_version_id=universe_version_id,
        version=UniverseVersion(
            universe_id=uuid4(),
            version="v1",
            declared_member_count=1,
            pit_certified=True,
        ),
        members=(UniverseMember(instrument_id=instrument_id),),
    )
    as_of = datetime(2026, 1, 2, tzinfo=UTC)
    run_fingerprint = research_run_fingerprint(
        experiment,
        as_of=as_of,
        universe_snapshot=snapshot,
    )
    run_id = uuid5(
        NAMESPACE_URL,
        f"hope:research-run:{experiment.experiment_id}:{run_fingerprint}",
    )
    canonical, evidence_fingerprint = research_result_fingerprint(
        _result(),
        execution_plan=stale_plan,
    )
    stored_evidence = ResearchRunEvidenceRecord(
        research_run_id=run_id,
        result_fingerprint=evidence_fingerprint,
        canonical_result=canonical,
    )

    class Experiments:
        def get(self, experiment_id):
            assert experiment_id == experiment.experiment_id
            return experiment

    class Runs:
        @staticmethod
        def deterministic_id(experiment_id, fingerprint):
            assert experiment_id == experiment.experiment_id
            assert fingerprint == run_fingerprint
            return run_id

        def claim(self, run):
            return False

    class Universes:
        def get(self, requested_id):
            assert requested_id == universe_version_id
            return snapshot

    class Contexts:
        def get(self, *args, **kwargs):
            raise AssertionError("market read must not occur before provenance rejection")

    class Evidence:
        def get(self, requested_id):
            assert requested_id == run_id
            return stored_evidence

        def persist(self, record):
            raise AssertionError("stored mismatched evidence must not be overwritten")

    executor = CertifiedResearchExecutor(
        ResearchExecutionRegistry(
            [
                ResearchExecutionImplementation(
                    strategy_version_id=strategy_version_id,
                    strategy_id=strategy_id,
                    strategy_version="1.0.0",
                    code_commit="current-commit",
                    execute=lambda plan, inputs: (_ for _ in ()).throw(
                        AssertionError("execution must not occur")
                    ),
                )
            ]
        )
    )
    orchestrator = CertifiedResearchRunOrchestrator(
        Experiments(),
        Runs(),
        Contexts(),
        Universes(),
        resolver,
        executor,
        Evidence(),
    )
    with pytest.raises(ValueError, match="EXECUTION_PROVENANCE_MISMATCH"):
        orchestrator.execute(experiment.experiment_id, as_of=as_of)
