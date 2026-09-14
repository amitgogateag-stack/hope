from datetime import datetime, timezone

import pytest

from hope.application.experiments.provenance import build_configuration_snapshot, build_provenance
from hope.domain.execution.models import Environment
from hope.domain.provenance.models import ProvenanceRecord


def test_configuration_snapshot_has_stable_hash():
    t = datetime(2026, 8, 28, tzinfo=timezone.utc)
    a = build_configuration_snapshot({"x": 1, "y": 2}, created_at=t)
    b = build_configuration_snapshot({"y": 2, "x": 1}, created_at=t)
    assert a.configuration_hash == b.configuration_hash
    assert a.created_at == t


def test_provenance_captures_configuration_and_parameter_hashes():
    p = build_provenance(
        dataset_version="D1", universe_version="U1", strategy_version="S1",
        parameters={"gap": 2}, cost_model_version="C1", execution_model_version="E1",
        code_commit="abc123", configuration={"gap": 2, "risk": 1},
        environment=Environment.BACKTEST,
    )
    assert len(p.configuration_hash) == 64
    assert len(p.parameter_snapshot_hash) == 64
    assert p.environment is Environment.BACKTEST


def test_configuration_snapshot_requires_timezone_aware_creation_time():
    with pytest.raises(ValueError, match="PROVENANCE_TIME_MUST_BE_TIMEZONE_AWARE"):
        build_configuration_snapshot(
            {"risk": 1},
            created_at=datetime(2026, 8, 28),
        )


def test_provenance_rejects_ambiguous_identity_and_malformed_hashes():
    values = {
        "dataset_version": "D1",
        "universe_version": "U1",
        "strategy_version": "S1",
        "parameter_snapshot_hash": "a" * 64,
        "cost_model_version": "C1",
        "execution_model_version": "E1",
        "code_commit": "abc123",
        "configuration_hash": "b" * 64,
        "environment": Environment.BACKTEST,
        "created_at": datetime(2026, 8, 28, tzinfo=timezone.utc),
    }

    with pytest.raises(ValueError, match="PROVENANCE_IDENTITY_NOT_CANONICAL"):
        ProvenanceRecord(**(values | {"dataset_version": " D1"}))
    with pytest.raises(ValueError, match="PROVENANCE_IDENTITY_REQUIRED"):
        ProvenanceRecord(**(values | {"code_commit": "   "}))
    with pytest.raises(ValueError, match="parameter_snapshot_hash"):
        ProvenanceRecord(**(values | {"parameter_snapshot_hash": "not-a-hash"}))
    with pytest.raises(ValueError, match="configuration_hash"):
        ProvenanceRecord(**(values | {"configuration_hash": "A" * 64}))


def test_provenance_requires_timezone_aware_creation_time():
    with pytest.raises(ValueError, match="PROVENANCE_TIME_MUST_BE_TIMEZONE_AWARE"):
        build_provenance(
            dataset_version="D1",
            universe_version="U1",
            strategy_version="S1",
            parameters={"gap": 2},
            cost_model_version="C1",
            execution_model_version="E1",
            code_commit="abc123",
            configuration={"gap": 2, "risk": 1},
            environment=Environment.BACKTEST,
            created_at=datetime(2026, 8, 28),
        )
