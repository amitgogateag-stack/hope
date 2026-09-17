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
        Decimal("100"), Decimal("100"), Decimal("0"), Decimal("0"), Decimal("0"),
        Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"),
    )
    return BacktestResult(
        initial_cash=Decimal("100"),
        final_state=PortfolioState(cash=Decimal("100"), positions={}),
        events=(), valuations=(), metrics=metrics,
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


def _research_provenance(plan: CertifiedExecutionPlan, **overrides) -> dict[str, str]:
    value = {
        "experiment_id": plan.experiment_id,
        "strategy_version_id": str(plan.strategy_version_id),
        "dataset_version_id": str(uuid4()),
        "universe_version_id": str(uuid4()),
        "universe_membership_hash": "1" * 64,
        "configuration_hash": plan.configuration_hash,
        "environment": "BACKTEST",
        "as_of": "2026-01-02T00:00:00+00:00",
        "run_fingerprint": "2" * 64,
    }
    value.update(overrides)
    return value


def test_certified_backtest_evidence_binds_execution_and_research_provenance() -> None:
    plan = _plan()
    provenance = _research_provenance(plan)
    canonical, fingerprint = research_result_fingerprint(
        _result(), execution_plan=plan, research_provenance=provenance
    )
    assert canonical["schema"] == CERTIFIED_BACKTEST_EVIDENCE_SCHEMA
    assert canonical["research_provenance"] == provenance
    assert canonical["backtest"]["schema"] == "hope.backtest-result.v1"
    assert len(fingerprint) == 64


def test_certified_backtest_evidence_identity_changes_when_code_or_run_provenance_changes() -> None:
    first_plan = _plan(code_commit="abc123")
    provenance = _research_provenance(first_plan)
    second_plan = CertifiedExecutionPlan(
        experiment_id=first_plan.experiment_id,
        strategy_version_id=first_plan.strategy_version_id,
        strategy_id=first_plan.strategy_id,
        strategy_version=first_plan.strategy_version,
        code_commit="def456",
        configuration_hash=first_plan.configuration_hash,
        configuration=first_plan.configuration,
    )
    _, first = research_result_fingerprint(_result(), execution_plan=first_plan, research_provenance=provenance)
    _, code_changed = research_result_fingerprint(_result(), execution_plan=second_plan, research_provenance=provenance)
    _, run_changed = research_result_fingerprint(
        _result(), execution_plan=first_plan,
        research_provenance=_research_provenance(first_plan, dataset_version_id=str(uuid4())),
    )
    assert first != code_changed
    assert first != run_changed


def test_certified_backtest_evidence_requires_run_provenance() -> None:
    with pytest.raises(ValueError, match="RUN_PROVENANCE_REQUIRED"):
        research_result_fingerprint(_result(), execution_plan=_plan())


def test_certified_backtest_evidence_rejects_cross_provenance_mismatch() -> None:
    plan = _plan()
    with pytest.raises(ValueError, match="CROSS_PROVENANCE_MISMATCH"):
        research_result_fingerprint(
            _result(), execution_plan=plan,
            research_provenance=_research_provenance(plan, configuration_hash="f" * 64),
        )


def test_certified_research_rejects_legacy_bare_and_v1_certified_evidence() -> None:
    legacy_bare, bare_fingerprint = research_result_fingerprint(_result())
    with pytest.raises(ValueError, match="CERTIFIED_BACKTEST_PROVENANCE_MISSING"):
        verify_research_result_evidence(legacy_bare, bare_fingerprint, reject_legacy_backtest=True)

    legacy_v1 = {
        "schema": "hope.certified-backtest-result.v1",
        "execution_provenance": {"legacy": True},
        "backtest": legacy_bare,
    }
    canonical_v1, v1_fingerprint = research_result_fingerprint(legacy_v1)
    with pytest.raises(ValueError, match="RUN_PROVENANCE_MISSING"):
        verify_research_result_evidence(canonical_v1, v1_fingerprint, reject_legacy_backtest=True)


def test_certified_evidence_verification_rejects_current_run_provenance_drift() -> None:
    plan = _plan()
    provenance = _research_provenance(plan)
    canonical, fingerprint = research_result_fingerprint(
        _result(), execution_plan=plan, research_provenance=provenance
    )
    with pytest.raises(ValueError, match="RESEARCH_PROVENANCE_MISMATCH"):
        verify_research_result_evidence(
            canonical, fingerprint, reject_legacy_backtest=True,
            execution_plan=plan,
            research_provenance={**provenance, "as_of": "2026-01-03T00:00:00+00:00"},
        )


def test_generic_research_evidence_contract_remains_unchanged() -> None:
    value = {"trades": 3, "net": 12}
    canonical, fingerprint = research_result_fingerprint(value, execution_plan=_plan())
    assert canonical == value
    assert verify_research_result_evidence(canonical, fingerprint, reject_legacy_backtest=True) == value
