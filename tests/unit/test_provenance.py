from datetime import datetime, timezone
from hope.application.experiments.provenance import build_configuration_snapshot, build_provenance
from hope.domain.execution.models import Environment


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
