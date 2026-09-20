from uuid import uuid4

import pytest

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.walk_forward_evidence import WalkForwardEvidenceProducer


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
                }
            },
        },
    }


def _window(train_start, train_end, test_start, test_end):
    return {
        "train_start": train_start,
        "train_end": train_end,
        "test_start": test_start,
        "test_end": test_end,
    }


def _fold(fold_id, window, source_run_id):
    return {"fold_id": fold_id, **window, "source_research_run_id": source_run_id}


def _protocol(ids, definitions, metrics=("total_pnl", "sharpe")):
    return {
        "fold_ids": ids,
        "fold_definitions": definitions,
        "metrics": list(metrics),
    }


def test_walk_forward_producer_resolves_sources_binds_windows_and_persists():
    repository = _Repository()
    source_1, source_2 = uuid4(), uuid4()
    resolver = _SourceResolver({
        source_1: _certified("10", "1.0"),
        source_2: _certified("12", "1.1"),
    })
    w1 = _window(
        "2026-01-01T00:00:00+00:00",
        "2026-02-01T00:00:00+00:00",
        "2026-02-01T00:00:00+00:00",
        "2026-03-01T00:00:00+00:00",
    )
    w2 = _window(
        "2026-02-01T00:00:00+00:00",
        "2026-03-01T00:00:00+00:00",
        "2026-03-01T00:00:00+00:00",
        "2026-04-01T00:00:00+00:00",
    )
    definitions = {"f1": w1, "f2": w2}

    record = WalkForwardEvidenceProducer(repository, resolver).produce(
        uuid4(),
        stage_protocol=_protocol(["f1", "f2"], definitions),
        folds=[_fold("f1", w1, source_1), _fold("f2", w2, source_2)],
    )

    assert resolver.resolved == [source_1, source_2]
    assert repository.persisted == [record]
    artifact = record.canonical_result
    assert artifact["folds"][0]["source_research_run_id"] == str(source_1)
    assert artifact["folds"][0]["test_end"] == w1["test_end"]
    assert artifact["result_fingerprint"] == configuration_hash(artifact["folds"])


def test_walk_forward_producer_requires_predeclared_windows():
    repository = _Repository()
    resolver = _SourceResolver({})
    with pytest.raises(ValueError, match="WALK_FORWARD_FOLD_DEFINITIONS_PREDECLARATION_REQUIRED"):
        WalkForwardEvidenceProducer(repository, resolver).produce(
            uuid4(),
            stage_protocol={"fold_ids": ["f1"], "metrics": ["total_pnl"]},
            folds=[],
        )


def test_walk_forward_producer_rejects_window_drift_before_resolution():
    repository = _Repository()
    source_run_id = uuid4()
    resolver = _SourceResolver({source_run_id: _certified()})
    declared = _window(
        "2026-01-01T00:00:00+00:00",
        "2026-02-01T00:00:00+00:00",
        "2026-02-01T00:00:00+00:00",
        "2026-03-01T00:00:00+00:00",
    )
    observed = dict(declared)
    observed["test_end"] = "2026-03-02T00:00:00+00:00"

    with pytest.raises(ValueError, match="WALK_FORWARD_PREDECLARED_WINDOW_MISMATCH:f1"):
        WalkForwardEvidenceProducer(repository, resolver).produce(
            uuid4(),
            stage_protocol=_protocol(["f1"], {"f1": declared}, ("total_pnl",)),
            folds=[_fold("f1", observed, source_run_id)],
        )
    assert resolver.resolved == []
    assert repository.persisted == []


def test_walk_forward_producer_propagates_verified_source_failure():
    repository = _Repository()
    source_run_id = uuid4()
    resolver = _SourceResolver({})
    definition = _window(
        "2026-01-01T00:00:00+00:00",
        "2026-02-01T00:00:00+00:00",
        "2026-02-01T00:00:00+00:00",
        "2026-03-01T00:00:00+00:00",
    )
    with pytest.raises(ValueError, match="VERIFIED_RESEARCH_SOURCE_RUN_MISSING"):
        WalkForwardEvidenceProducer(repository, resolver).produce(
            uuid4(),
            stage_protocol=_protocol(["f1"], {"f1": definition}, ("total_pnl",)),
            folds=[_fold("f1", definition, source_run_id)],
        )
    assert repository.persisted == []
