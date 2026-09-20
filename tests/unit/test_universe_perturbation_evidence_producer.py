from uuid import uuid4

import pytest

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.universe_perturbation_evidence import (
    UniversePerturbationEvidenceProducer,
)


class _Repository:
    def __init__(self):
        self.persisted = []

    def persist(self, record):
        self.persisted.append(record)
        return True


class _SourceResolver:
    def __init__(self, evidence_by_run_id):
        self.evidence_by_run_id = evidence_by_run_id
        self.resolved = []

    def resolve(self, run_id):
        self.resolved.append(run_id)
        if run_id not in self.evidence_by_run_id:
            raise ValueError("VERIFIED_RESEARCH_SOURCE_RUN_MISSING")
        return self.evidence_by_run_id[run_id]


def _definition(method="baseline", version_id=None, membership_hash=None, as_of=None):
    return {
        "method": method,
        "universe_version_id": version_id or str(uuid4()),
        "universe_membership_hash": membership_hash or ("a" * 64),
        "as_of": as_of or "2026-01-31T00:00:00+00:00",
    }


def _certified(definition, pnl="10", sharpe="1.0"):
    return {
        "schema": "hope.certified-backtest-result.v3",
        "execution_provenance": {"verified": True},
        "research_provenance": {
            "universe_version_id": definition["universe_version_id"],
            "universe_membership_hash": definition["universe_membership_hash"],
            "as_of": definition["as_of"],
        },
        "backtest": {
            "schema": "hope.backtest-result.v1",
            "result": {
                "metrics": {
                    "initial_equity": "100",
                    "final_equity": "110",
                    "total_pnl": pnl,
                    "total_return": "0.1",
                    "max_drawdown": "0.02",
                    "volatility": "0.1",
                    "sharpe": sharpe,
                    "downside_deviation": "0.05",
                    "sortino": "1.2",
                }
            },
        },
    }


def _protocol(ids, definitions, metrics=("total_pnl", "sharpe")):
    return {
        "perturbation_ids": ids,
        "perturbation_definitions": definitions,
        "metrics": list(metrics),
    }


def test_universe_perturbation_producer_resolves_sources_binds_protocol_pit_and_persists():
    repository = _Repository()
    baseline = _definition()
    drop10 = _definition(method="drop_random_fraction", membership_hash="b" * 64)
    definitions = {"baseline": baseline, "drop10": drop10}
    baseline_run, drop10_run = uuid4(), uuid4()
    resolver = _SourceResolver({
        baseline_run: _certified(baseline, "10", "1.0"),
        drop10_run: _certified(drop10, "8", "0.8"),
    })

    record = UniversePerturbationEvidenceProducer(repository, resolver).produce(
        uuid4(),
        stage_protocol=_protocol(["baseline", "drop10"], definitions),
        perturbations=[
            {"perturbation_id": "baseline", "universe_definition": baseline, "source_research_run_id": baseline_run},
            {"perturbation_id": "drop10", "universe_definition": drop10, "source_research_run_id": drop10_run},
        ],
    )

    assert resolver.resolved == [baseline_run, drop10_run]
    assert repository.persisted == [record]
    artifact = record.canonical_result
    assert artifact["perturbations"][1]["source_research_run_id"] == str(drop10_run)
    assert artifact["perturbations"][1]["universe_definition"] == drop10
    assert artifact["result_fingerprint"] == configuration_hash(artifact["perturbations"])


def test_universe_perturbation_producer_requires_predeclared_definitions():
    repository = _Repository()
    resolver = _SourceResolver({})
    with pytest.raises(
        ValueError,
        match="UNIVERSE_PERTURBATION_DEFINITIONS_PREDECLARATION_REQUIRED",
    ):
        UniversePerturbationEvidenceProducer(repository, resolver).produce(
            uuid4(),
            stage_protocol={"perturbation_ids": ["baseline"], "metrics": ["total_pnl"]},
            perturbations=[],
        )


def test_universe_perturbation_producer_rejects_definition_drift_before_resolution():
    repository = _Repository()
    baseline = _definition()
    drifted = dict(baseline)
    drifted["universe_membership_hash"] = "b" * 64
    source_run_id = uuid4()
    resolver = _SourceResolver({source_run_id: _certified(baseline)})

    with pytest.raises(
        ValueError,
        match="UNIVERSE_PERTURBATION_PREDECLARED_DEFINITION_MISMATCH:baseline",
    ):
        UniversePerturbationEvidenceProducer(repository, resolver).produce(
            uuid4(),
            stage_protocol=_protocol(["baseline"], {"baseline": baseline}, ("total_pnl",)),
            perturbations=[{
                "perturbation_id": "baseline",
                "universe_definition": drifted,
                "source_research_run_id": source_run_id,
            }],
        )
    assert resolver.resolved == []
    assert repository.persisted == []


def test_universe_perturbation_producer_rejects_pit_binding_drift():
    repository = _Repository()
    definition = _definition()
    source_run_id = uuid4()
    certified = _certified(definition)
    certified["research_provenance"]["universe_membership_hash"] = "c" * 64
    resolver = _SourceResolver({source_run_id: certified})

    with pytest.raises(
        ValueError,
        match="UNIVERSE_PERTURBATION_PIT_BINDING_MISMATCH:baseline:universe_membership_hash",
    ):
        UniversePerturbationEvidenceProducer(repository, resolver).produce(
            uuid4(),
            stage_protocol=_protocol(["baseline"], {"baseline": definition}, ("total_pnl",)),
            perturbations=[{
                "perturbation_id": "baseline",
                "universe_definition": definition,
                "source_research_run_id": source_run_id,
            }],
        )
    assert repository.persisted == []


def test_universe_perturbation_producer_rejects_noncanonical_predeclared_definition():
    repository = _Repository()
    definition = _definition()
    definition["universe_membership_hash"] = "not-a-hash"
    resolver = _SourceResolver({})

    with pytest.raises(
        ValueError,
        match="UNIVERSE_PERTURBATION_DEFINITION_INVALID:baseline",
    ):
        UniversePerturbationEvidenceProducer(repository, resolver).produce(
            uuid4(),
            stage_protocol=_protocol(["baseline"], {"baseline": definition}, ("total_pnl",)),
            perturbations=[],
        )
    assert resolver.resolved == []
