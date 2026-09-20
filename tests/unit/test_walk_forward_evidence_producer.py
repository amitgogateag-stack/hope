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


class _SourceResolver:
    def __init__(self, evidence_by_run_id):
        self.evidence_by_run_id = evidence_by_run_id
        self.resolved = []

    def resolve(self, run_id):
        self.resolved.append(run_id)
        if run_id not in self.evidence_by_run_id:
            raise ValueError("VERIFIED_RESEARCH_SOURCE_RUN_MISSING")
        return self.evidence_by_run_id[run_id]


def _certified(pnl="10", sharpe="1.0"):
    return {
        "schema": "hope.certified-backtest-result.v3",
        "execution_provenance": {"verified": True},
        "research_provenance": {"verified": True},
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
                },
            },
        },
    }


def _fold(fold_id, train_start, train_end, test_start, test_end, source_run_id):
    return {
        "fold_id": fold_id,
        "train_start": train_start,
        "train_end": train_end,
        "test_start": test_start,
        "test_end": test_end,
        "source_research_run_id": source_run_id,
    }


def test_walk_forward_producer_resolves_durable_sources_projects_metrics_and_persists():
    repository = _Repository()
    source_1, source_2 = uuid4(), uuid4()
    resolver = _SourceResolver({
        source_1: _certified(pnl="10", sharpe="1.0"),
        source_2: _certified(pnl="12", sharpe="1.1"),
    })
    producer = WalkForwardEvidenceProducer(repository, resolver)
    folds = [
        _fold(
            "f1",
            "2026-01-01T00:00:00+00:00",
            "2026-02-01T00:00:00+00:00",
            "2026-02-01T00:00:00+00:00",
            "2026-03-01T00:00:00+00:00",
            source_1,
        ),
        _fold(
            "f2",
            "2026-02-01T00:00:00+00:00",
            "2026-03-01T00:00:00+00:00",
            "2026-03-01T00:00:00+00:00",
            "2026-04-01T00:00:00+00:00",
            source_2,
        ),
    ]

    record = producer.produce(
        uuid4(),
        stage_protocol={"fold_ids": ["f1", "f2"], "metrics": ["total_pnl", "sharpe"]},
        folds=folds,
    )

    assert resolver.resolved == [source_1, source_2]
    assert repository.persisted == [record]
    artifact = record.canonical_result
    assert artifact["schema"] == "hope.walk-forward-evidence.v1"
    assert artifact["folds"][0]["source_research_run_id"] == str(source_1)
    assert artifact["folds"][0]["metrics"] == {"total_pnl": "10", "sharpe": "1.0"}
    assert artifact["result_fingerprint"] == configuration_hash(artifact["folds"])


def test_walk_forward_producer_rejects_missing_source_run_id_before_resolution():
    repository = _Repository()
    resolver = _SourceResolver({})
    producer = WalkForwardEvidenceProducer(repository, resolver)
    fold = {
        "fold_id": "f1",
        "train_start": "2026-01-01T00:00:00+00:00",
        "train_end": "2026-02-01T00:00:00+00:00",
        "test_start": "2026-02-01T00:00:00+00:00",
        "test_end": "2026-03-01T00:00:00+00:00",
    }

    with pytest.raises(ValueError, match="WALK_FORWARD_SOURCE_RUN_ID_REQUIRED:f1"):
        producer.produce(
            uuid4(),
            stage_protocol={"fold_ids": ["f1"], "metrics": ["total_pnl"]},
            folds=[fold],
        )
    assert resolver.resolved == []
    assert repository.persisted == []


def test_walk_forward_producer_propagates_verified_source_failure():
    repository = _Repository()
    source_run_id = uuid4()
    resolver = _SourceResolver({})
    producer = WalkForwardEvidenceProducer(repository, resolver)
    fold = _fold(
        "f1",
        "2026-01-01T00:00:00+00:00",
        "2026-02-01T00:00:00+00:00",
        "2026-02-01T00:00:00+00:00",
        "2026-03-01T00:00:00+00:00",
        source_run_id,
    )

    with pytest.raises(ValueError, match="VERIFIED_RESEARCH_SOURCE_RUN_MISSING"):
        producer.produce(
            uuid4(),
            stage_protocol={"fold_ids": ["f1"], "metrics": ["total_pnl"]},
            folds=[fold],
        )
    assert repository.persisted == []


def test_walk_forward_producer_rejects_fold_order_drift():
    repository = _Repository()
    s1, s2 = uuid4(), uuid4()
    resolver = _SourceResolver({s1: _certified(), s2: _certified()})
    producer = WalkForwardEvidenceProducer(repository, resolver)
    folds = [
        _fold(
            "f2",
            "2026-01-01T00:00:00+00:00",
            "2026-02-01T00:00:00+00:00",
            "2026-02-01T00:00:00+00:00",
            "2026-03-01T00:00:00+00:00",
            s1,
        ),
        _fold(
            "f1",
            "2026-02-01T00:00:00+00:00",
            "2026-03-01T00:00:00+00:00",
            "2026-03-01T00:00:00+00:00",
            "2026-04-01T00:00:00+00:00",
            s2,
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
    s1, s2 = uuid4(), uuid4()
    resolver = _SourceResolver({s1: _certified(), s2: _certified()})
    producer = WalkForwardEvidenceProducer(repository, resolver)
    folds = [
        _fold(
            "f1",
            "2026-01-01T00:00:00+00:00",
            "2026-02-01T00:00:00+00:00",
            "2026-02-01T00:00:00+00:00",
            "2026-03-15T00:00:00+00:00",
            s1,
        ),
        _fold(
            "f2",
            "2026-02-01T00:00:00+00:00",
            "2026-03-01T00:00:00+00:00",
            "2026-03-01T00:00:00+00:00",
            "2026-04-01T00:00:00+00:00",
            s2,
        ),
    ]
    with pytest.raises(ValueError, match="WALK_FORWARD_TEST_WINDOWS_OVERLAP"):
        producer.produce(
            uuid4(),
            stage_protocol={"fold_ids": ["f1", "f2"], "metrics": ["total_pnl"]},
            folds=folds,
        )
