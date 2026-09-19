from types import SimpleNamespace
from uuid import uuid4

import pytest

from hope.application.experiments.walk_forward_evidence import WalkForwardEvidenceProducer
from hope.application.experiments.config_hash import configuration_hash


class _Repository:
    def __init__(self):
        self.persisted = []

    def persist(self, record):
        self.persisted.append(record)
        return True


def _certified(pnl="10", sharpe="1.0"):
    return {
        "schema": "hope.certified-backtest-result.v3",
        "execution_provenance": {},
        "research_provenance": {},
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


def _fold(fold_id, train_start, train_end, test_start, test_end, pnl):
    return {
        "fold_id": fold_id,
        "train_start": train_start,
        "train_end": train_end,
        "test_start": test_start,
        "test_end": test_end,
        "certified_result": _certified(pnl=pnl),
    }


def test_walk_forward_producer_projects_declared_metrics_and_persists():
    repository = _Repository()
    producer = WalkForwardEvidenceProducer(repository)
    folds = [
        _fold(
            "f1",
            "2026-01-01T00:00:00+00:00",
            "2026-02-01T00:00:00+00:00",
            "2026-02-01T00:00:00+00:00",
            "2026-03-01T00:00:00+00:00",
            "10",
        ),
        _fold(
            "f2",
            "2026-02-01T00:00:00+00:00",
            "2026-03-01T00:00:00+00:00",
            "2026-03-01T00:00:00+00:00",
            "2026-04-01T00:00:00+00:00",
            "12",
        ),
    ]

    record = producer.produce(
        uuid4(),
        stage_protocol={"fold_ids": ["f1", "f2"], "metrics": ["total_pnl", "sharpe"]},
        folds=folds,
    )

    assert repository.persisted == [record]
    artifact = record.canonical_result
    assert artifact["schema"] == "hope.walk-forward-evidence.v1"
    assert artifact["folds"][0]["metrics"] == {"total_pnl": "10", "sharpe": "1.0"}
    assert artifact["result_fingerprint"] == configuration_hash(artifact["folds"])
    assert set(artifact["folds"][0]["metrics"]) == {"total_pnl", "sharpe"}


def test_walk_forward_producer_rejects_noncertified_fold_result():
    repository = _Repository()
    producer = WalkForwardEvidenceProducer(repository)
    fold = _fold(
        "f1",
        "2026-01-01T00:00:00+00:00",
        "2026-02-01T00:00:00+00:00",
        "2026-02-01T00:00:00+00:00",
        "2026-03-01T00:00:00+00:00",
        "10",
    )
    fold["certified_result"] = {"schema": "hope.backtest-result.v1"}

    with pytest.raises(ValueError, match="WALK_FORWARD_CERTIFIED_RESULT_REQUIRED:f1"):
        producer.produce(
            uuid4(),
            stage_protocol={"fold_ids": ["f1"], "metrics": ["total_pnl"]},
            folds=[fold],
        )
    assert repository.persisted == []


def test_walk_forward_producer_rejects_fold_order_drift():
    repository = _Repository()
    producer = WalkForwardEvidenceProducer(repository)
    folds = [
        _fold(
            "f2",
            "2026-01-01T00:00:00+00:00",
            "2026-02-01T00:00:00+00:00",
            "2026-02-01T00:00:00+00:00",
            "2026-03-01T00:00:00+00:00",
            "10",
        ),
        _fold(
            "f1",
            "2026-02-01T00:00:00+00:00",
            "2026-03-01T00:00:00+00:00",
            "2026-03-01T00:00:00+00:00",
            "2026-04-01T00:00:00+00:00",
            "12",
        ),
    ]
    with pytest.raises(ValueError, match="WALK_FORWARD_SOURCE_FOLDS_MISMATCH"):
        producer.produce(
            uuid4(),
            stage_protocol={"fold_ids": ["f1", "f2"], "metrics": ["total_pnl"]},
            folds=folds,
        )


def test_walk_forward_producer_rejects_overlapping_test_windows():
    repository = _Repository()
    producer = WalkForwardEvidenceProducer(repository)
    folds = [
        _fold(
            "f1",
            "2026-01-01T00:00:00+00:00",
            "2026-02-01T00:00:00+00:00",
            "2026-02-01T00:00:00+00:00",
            "2026-03-15T00:00:00+00:00",
            "10",
        ),
        _fold(
            "f2",
            "2026-02-01T00:00:00+00:00",
            "2026-03-01T00:00:00+00:00",
            "2026-03-01T00:00:00+00:00",
            "2026-04-01T00:00:00+00:00",
            "12",
        ),
    ]
    with pytest.raises(ValueError, match="WALK_FORWARD_TEST_WINDOWS_OVERLAP"):
        producer.produce(
            uuid4(),
            stage_protocol={"fold_ids": ["f1", "f2"], "metrics": ["total_pnl"]},
            folds=folds,
        )
