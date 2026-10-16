"""Projection schema preserves unknown/zero distinctions and aggregate privacy."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json

import pytest

from hope.api.dashboard_projection import EXPERIMENT_STATES, VERDICTS, verified_projections
from hope.api.dashboard_server import _verified_snapshot
from hope.api.dashboard_upgrade_probe import _validate_paper


def projections():
    return {
        "datasets": {"status": "AVAILABLE", "registered": 2, "pit_certified": 1},
        "strategies": {"status": "AVAILABLE", "versions": 1},
        "experiments": {"status": "AVAILABLE", "total": 1, "by_status": {k: int(k == "CREATED") for k in EXPERIMENT_STATES}},
        "decisions": {"status": "AVAILABLE", "total": 1, "by_verdict": {k: int(k == "INCONCLUSIVE") for k in VERDICTS}},
        "paper_activity": {"status": "AVAILABLE", "last_completed_at": None, "succeeded_no_trade": 0},
        "market_data": {"status": "AVAILABLE", "bar_count": 0, "latest_event_at": None, "latest_available_at": None, "freshness_policy": "UNKNOWN"},
        "portfolio": {"status": "UNAVAILABLE"},
    }


def snapshot():
    return {
        "schema_version": 1, "source": "hope_postgresql", "generated_at": datetime.now(timezone.utc).isoformat(),
        "research": {"run_count": 0}, "paper": {"environment_state": "HALTED", "job_runs": {"total": 0, "claimed": 0, "succeeded": 0, "failed": 0}},
        "projections": projections(),
    }


def test_projection_fields_are_whitelisted_and_unavailable_never_becomes_zero(tmp_path):
    data = snapshot()
    data["projections"]["datasets"]["database_password"] = "NEVER_EXPOSE"
    data["projections"]["decisions"]["by_verdict"]["private"] = "NEVER_EXPOSE"
    data["projections"]["portfolio"]["equity"] = "NEVER_EXPOSE"
    data["projections"]["private_positions"] = ["NEVER_EXPOSE"]
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(data))
    body, _ = _verified_snapshot(path)
    assert b"NEVER_EXPOSE" not in body
    safe = json.loads(body)
    assert safe["projections"] == projections()
    _validate_paper(safe)
    with pytest.raises(ValueError, match="PROJECTIONS_INVALID"):
        _validate_paper(data)  # Unsanitized HTTP responses fail acceptance.
    unavailable = verified_projections({}, datetime.now(timezone.utc))
    assert all(item == {"status": "UNAVAILABLE"} for item in unavailable.values())


@pytest.mark.parametrize("section,change", [
    ("datasets", {"registered": True}), ("datasets", {"pit_certified": 3}),
    ("strategies", {"versions": 9007199254740992}),
    ("experiments", {"total": 8}), ("experiments", {"by_status": {}}),
    ("decisions", {"by_verdict": None}),
    ("paper_activity", {"succeeded_no_trade": -1}),
    ("paper_activity", {"last_completed_at": "not-a-date"}),
    ("paper_activity", {"last_completed_at": "2026-10-16T12:00:00"}),
    ("paper_activity", {"last_completed_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}),
    ("market_data", {"freshness_policy": "FRESH"}),
    ("market_data", {"bar_count": 1}),
    ("market_data", {"latest_event_at": "2026-01-01T00:00:00+00:00"}),
    ("portfolio", {"status": "AVAILABLE"}), ("strategies", {"status": "CURRENT"}),
])
def test_partial_corrupt_or_unverified_projection_rejected(section, change):
    data = projections()
    data[section].update(deepcopy(change))
    with pytest.raises(ValueError):
        verified_projections(data, datetime.now(timezone.utc))


@pytest.mark.parametrize("value", [None, [], "private"])
def test_wrong_projection_shape_rejected(value):
    with pytest.raises(ValueError):
        verified_projections(value, datetime.now(timezone.utc))


def test_market_timestamp_order_compares_instants_across_offsets():
    value = projections()
    value["market_data"].update(bar_count=1, latest_event_at="2026-01-01T12:00:00+05:30", latest_available_at="2026-01-01T07:00:00+00:00")
    safe = verified_projections(value, datetime.now(timezone.utc))
    assert safe["market_data"]["latest_event_at"] == "2026-01-01T06:30:00+00:00"
    value["market_data"]["latest_available_at"] = "2026-01-01T01:00:00-05:00"
    with pytest.raises(ValueError, match="TIME_INCONSISTENT"):
        verified_projections(value, datetime.now(timezone.utc))
