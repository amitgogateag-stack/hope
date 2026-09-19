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
        "execution_provenance": {},
        "research_provenance": {
            "universe_version_id": definition["universe_version_id"],
            "universe_membership_hash": definition["universe_membership_hash"],
            "as_of": definition["as_of"],
        },
        "backtest": {
            "schema": "hope.backtest-result.v1",
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
            },
        },
    }


def test_universe_perturbation_producer_binds_pit_identity_and_persists():
    repository = _Repository()
    baseline = _definition()
    drop10 = _definition(method="drop_random_fraction", membership_hash="b" * 64)

    record = UniversePerturbationEvidenceProducer(repository).produce(
        uuid4(),
        stage_protocol={
            "perturbation_ids": ["baseline", "drop10"],
            "metrics": ["total_pnl", "sharpe"],
        },
        perturbations=[
            {
                "perturbation_id": "baseline",
                "universe_definition": baseline,
                "certified_result": _certified(baseline, "10", "1.0"),
            },
            {
                "perturbation_id": "drop10",
                "universe_definition": drop10,
                "certified_result": _certified(drop10, "8", "0.8"),
            },
        ],
    )

    assert repository.persisted == [record]
    artifact = record.canonical_result
    assert artifact["schema"] == "hope.universe-perturbation-evidence.v1"
    assert artifact["perturbations"][1]["universe_definition"] == drop10
    assert artifact["result_fingerprint"] == configuration_hash(
        artifact["perturbations"]
    )
    assert "winner" not in artifact
    assert "best" not in artifact


def test_universe_perturbation_producer_rejects_pit_binding_drift():
    repository = _Repository()
    definition = _definition()
    certified = _certified(definition)
    certified["research_provenance"]["universe_membership_hash"] = "c" * 64

    with pytest.raises(
        ValueError,
        match="UNIVERSE_PERTURBATION_PIT_BINDING_MISMATCH:baseline:universe_membership_hash",
    ):
        UniversePerturbationEvidenceProducer(repository).produce(
            uuid4(),
            stage_protocol={
                "perturbation_ids": ["baseline"],
                "metrics": ["total_pnl"],
            },
            perturbations=[
                {
                    "perturbation_id": "baseline",
                    "universe_definition": definition,
                    "certified_result": certified,
                }
            ],
        )
    assert repository.persisted == []


def test_universe_perturbation_producer_requires_canonical_pit_definition():
    repository = _Repository()
    definition = _definition()
    definition["universe_membership_hash"] = "not-a-hash"

    with pytest.raises(
        ValueError,
        match="UNIVERSE_PERTURBATION_DEFINITION_INVALID:baseline",
    ):
        UniversePerturbationEvidenceProducer(repository).produce(
            uuid4(),
            stage_protocol={
                "perturbation_ids": ["baseline"],
                "metrics": ["total_pnl"],
            },
            perturbations=[
                {
                    "perturbation_id": "baseline",
                    "universe_definition": definition,
                    "certified_result": _certified(_definition()),
                }
            ],
        )
    assert repository.persisted == []


def test_universe_perturbation_producer_rejects_identity_or_order_drift():
    repository = _Repository()
    first = _definition()
    second = _definition(membership_hash="b" * 64)
    with pytest.raises(
        ValueError,
        match="UNIVERSE_PERTURBATION_SOURCE_IDS_MISMATCH",
    ):
        UniversePerturbationEvidenceProducer(repository).produce(
            uuid4(),
            stage_protocol={
                "perturbation_ids": ["baseline", "drop10"],
                "metrics": ["total_pnl"],
            },
            perturbations=[
                {
                    "perturbation_id": "drop10",
                    "universe_definition": second,
                    "certified_result": _certified(second),
                },
                {
                    "perturbation_id": "baseline",
                    "universe_definition": first,
                    "certified_result": _certified(first),
                },
            ],
        )
    assert repository.persisted == []
