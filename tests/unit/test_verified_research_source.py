import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.research_runs import research_result_fingerprint
from hope.application.experiments.verified_source import (
    VerifiedCertifiedResearchSourceResolver,
)
from hope.infrastructure.repositories.execution_provenance import CertifiedExecutionPlan


class _Repo:
    def __init__(self, value):
        self.value = value

    def get(self, key):
        return self.value


class _PlanResolver:
    def __init__(self, plan):
        self.plan = plan

    def resolve(self, experiment):
        return self.plan


def _fixture():
    run_id = uuid4()
    strategy_version_id = uuid4()
    strategy_id = uuid4()
    dataset_version_id = uuid4()
    universe_version_id = uuid4()
    configuration = {"risk": {"per_trade": "0.01"}}
    configuration_hash_value = configuration_hash(configuration)
    as_of = datetime(2026, 1, 31, tzinfo=timezone.utc)

    experiment = SimpleNamespace(
        experiment_id="EXP-1",
        strategy_version_id=strategy_version_id,
        dataset_version_id=dataset_version_id,
        universe_version_id=universe_version_id,
        configuration_hash=configuration_hash_value,
        environment="BACKTEST",
        invalidated_at=None,
    )
    identity = {
        "experiment_id": experiment.experiment_id,
        "strategy_version_id": str(strategy_version_id),
        "dataset_version_id": str(dataset_version_id),
        "universe_version_id": str(universe_version_id),
        "universe_membership_hash": "a" * 64,
        "market_data_manifest_hash": "b" * 64,
        "configuration_hash": configuration_hash_value,
        "environment": "BACKTEST",
        "as_of": as_of.isoformat(),
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    run_fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    research = {**identity, "run_fingerprint": run_fingerprint}
    run = SimpleNamespace(
        research_run_id=run_id,
        experiment_id="EXP-1",
        run_fingerprint=run_fingerprint,
        as_of=as_of,
    )
    plan = CertifiedExecutionPlan(
        experiment_id="EXP-1",
        strategy_version_id=strategy_version_id,
        strategy_id=strategy_id,
        strategy_version="1.0.0",
        code_commit="abc123",
        configuration_hash=configuration_hash_value,
        configuration=configuration,
    )
    certified = {
        "schema": "hope.certified-backtest-result.v3",
        "execution_provenance": {
            "experiment_id": "EXP-1",
            "strategy_version_id": str(strategy_version_id),
            "strategy_id": str(strategy_id),
            "strategy_version": "1.0.0",
            "code_commit": "abc123",
            "configuration_hash": configuration_hash_value,
        },
        "research_provenance": research,
        "backtest": {
            "schema": "hope.backtest-result.v1",
            "result": {"metrics": {}},
        },
    }
    canonical, result_fingerprint = research_result_fingerprint(certified)
    evidence = SimpleNamespace(
        research_run_id=run_id,
        result_fingerprint=result_fingerprint,
        canonical_result=canonical,
    )
    return run_id, experiment, run, evidence, plan


def _resolver(experiment, run, evidence, plan):
    return VerifiedCertifiedResearchSourceResolver(
        experiment_repository=_Repo(experiment),
        run_repository=_Repo(run),
        evidence_repository=_Repo(evidence),
        execution_plan_resolver=_PlanResolver(plan),
    )


def test_verified_source_resolver_returns_only_fully_verified_durable_evidence():
    run_id, experiment, run, evidence, plan = _fixture()

    verified = _resolver(experiment, run, evidence, plan).resolve(run_id)

    assert verified == evidence.canonical_result


def test_verified_source_resolver_rejects_tampered_durable_result_fingerprint():
    run_id, experiment, run, evidence, plan = _fixture()
    evidence.result_fingerprint = "f" * 64

    with pytest.raises(
        ValueError,
        match="RESEARCH_RUN_EVIDENCE_FINGERPRINT_MISMATCH",
    ):
        _resolver(experiment, run, evidence, plan).resolve(run_id)


def test_verified_source_resolver_rejects_wrong_run_identity():
    run_id, experiment, run, evidence, plan = _fixture()
    run.research_run_id = uuid4()

    with pytest.raises(
        ValueError,
        match="VERIFIED_RESEARCH_SOURCE_RUN_IDENTITY_MISMATCH",
    ):
        _resolver(experiment, run, evidence, plan).resolve(run_id)


def test_verified_source_resolver_rejects_research_provenance_drift():
    run_id, experiment, run, evidence, plan = _fixture()
    tampered = dict(evidence.canonical_result)
    tampered["research_provenance"] = dict(tampered["research_provenance"])
    tampered["research_provenance"]["universe_membership_hash"] = "c" * 64
    canonical, fingerprint = research_result_fingerprint(tampered)
    evidence.canonical_result = canonical
    evidence.result_fingerprint = fingerprint

    with pytest.raises(
        ValueError,
        match="VERIFIED_RESEARCH_SOURCE_RUN_FINGERPRINT_MISMATCH",
    ):
        _resolver(experiment, run, evidence, plan).resolve(run_id)


def test_verified_source_resolver_rejects_execution_provenance_drift():
    run_id, experiment, run, evidence, plan = _fixture()
    tampered = dict(evidence.canonical_result)
    tampered["execution_provenance"] = dict(tampered["execution_provenance"])
    tampered["execution_provenance"]["code_commit"] = "different"
    canonical, fingerprint = research_result_fingerprint(tampered)
    evidence.canonical_result = canonical
    evidence.result_fingerprint = fingerprint

    with pytest.raises(
        ValueError,
        match="CERTIFIED_BACKTEST_EVIDENCE_EXECUTION_PROVENANCE_MISMATCH",
    ):
        _resolver(experiment, run, evidence, plan).resolve(run_id)
