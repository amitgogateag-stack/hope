from decimal import Decimal
from uuid import uuid4

import pytest

from hope.application.backtests.analytics import BacktestMetrics
from hope.application.backtests.certified_evidence import CERTIFIED_BACKTEST_EVIDENCE_SCHEMA
from hope.application.backtests.engine import BacktestResult
from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.research_runs import (
    research_result_fingerprint,
    verify_research_result_evidence,
)
from hope.domain.portfolio.ledger import PortfolioState
from hope.infrastructure.repositories.execution_provenance import CertifiedExecutionPlan


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


def _plan(*, code_commit: str = "abc123", configuration=None) -> CertifiedExecutionPlan:
    configuration = configuration or {"execution": {"latency_ms": 0}, "strategy": {"lookback": 20}}
    return CertifiedExecutionPlan(
        experiment_id="exp-certified-evidence",
        strategy_version_id=uuid4(),
        strategy_id=uuid4(),
        strategy_version="1.0.0",
        code_commit=code_commit,
        configuration_hash=configuration_hash(configuration),
        configuration=configuration,
    )


def test_certified_backtest_evidence_binds_resolved_execution_provenance() -> None:
    plan = _plan()
    canonical, fingerprint = research_result_fingerprint(_result(), execution_plan=plan)
    assert canonical["schema"] == CERTIFIED_BACKTEST_EVIDENCE_SCHEMA
    assert canonical["execution_provenance"] == {
        "experiment_id": plan.experiment_id,
        "strategy_version_id": str(plan.strategy_version_id),
        "strategy_id": str(plan.strategy_id),
        "strategy_version": plan.strategy_version,
        "code_commit": plan.code_commit,
        "configuration_hash": plan.configuration_hash,
    }
    assert canonical["backtest"]["schema"] == "hope.backtest-result.v1"
    assert len(fingerprint) == 64


def test_certified_backtest_evidence_identity_changes_when_code_provenance_changes() -> None:
    first_plan = _plan(code_commit="abc123")
    second_plan = CertifiedExecutionPlan(
        experiment_id=first_plan.experiment_id,
        strategy_version_id=first_plan.strategy_version_id,
        strategy_id=first_plan.strategy_id,
        strategy_version=first_plan.strategy_version,
        code_commit="def456",
        configuration_hash=first_plan.configuration_hash,
        configuration=first_plan.configuration,
    )
    _, first = research_result_fingerprint(_result(), execution_plan=first_plan)
    _, second = research_result_fingerprint(_result(), execution_plan=second_plan)
    assert first != second


def test_certified_backtest_evidence_rejects_forged_configuration_content() -> None:
    plan = _plan()
    forged = CertifiedExecutionPlan(
        experiment_id=plan.experiment_id,
        strategy_version_id=plan.strategy_version_id,
        strategy_id=plan.strategy_id,
        strategy_version=plan.strategy_version,
        code_commit=plan.code_commit,
        configuration_hash=plan.configuration_hash,
        configuration={"strategy": {"lookback": 99}},
    )
    with pytest.raises(ValueError, match="CONFIGURATION_HASH_MISMATCH"):
        research_result_fingerprint(_result(), execution_plan=forged)


def test_certified_research_rejects_legacy_backtest_evidence_on_restart() -> None:
    legacy, fingerprint = research_result_fingerprint(_result())
    with pytest.raises(ValueError, match="CERTIFIED_BACKTEST_PROVENANCE_MISSING"):
        verify_research_result_evidence(
            legacy,
            fingerprint,
            reject_legacy_backtest=True,
        )


def test_generic_research_evidence_contract_remains_unchanged() -> None:
    value = {"trades": 3, "net": 12}
    plan = _plan()
    canonical, fingerprint = research_result_fingerprint(value, execution_plan=plan)
    assert canonical == value
    assert verify_research_result_evidence(canonical, fingerprint, reject_legacy_backtest=True) == value
