"""M2 projections expose bounded vocabularies, never infer authorization truth."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from hope.api.dashboard_projection import BLOCKED_CODES, RISK_BLOCK_REASONS, SECTIONS, verified_projections
from hope.api.dashboard_server import _verified_snapshot
from hope.api.dashboard_upgrade_probe import _validate_paper


def projection():
    return {
        **{name: {"status": "UNAVAILABLE"} for name in SECTIONS},
        "strategy_catalog": {"status": "AVAILABLE", "total": 1, "truncated": False, "rows": [
            {"strategy_version_id": "a" * 8 + "-" + "b" * 4 + "-" + "c" * 4 + "-" + "d" * 4 + "-" + "e" * 12,
             "family_code": "TIME_SERIES_TREND", "candidate_state": "OPERATIONAL_CANDIDATE", "markets": ["INDIA", "USA"]}]},
        "dataset_certification": {"status": "AVAILABLE", "version_total": 3, "sealed_versions": 2, "pit_certified_versions": 1, "manifest_backed_sealed_versions": 1},
        "execution_ledger": {"status": "AVAILABLE", "fills": 2, "applied_fills": 1, "fill_applications": 1, "accounting_receipts": 0, "fills_without_application": 1, "applications_without_pnl": 1},
        "blocked_runs": {"status": "AVAILABLE", "total": 1, "by_reason": {key: int(key == "OTHER") for key in BLOCKED_CODES}},
        "decision_activity": {"status": "AVAILABLE", "no_trade_records": 0, "claimed_no_trade": 0, "latest_decision_at": None},
        "certified_chain": {"status": "AVAILABLE", "authorization_receipts": 0, "latest_authorization_at": None, "operational_classifications": 1, "eligibility_policy": "UNKNOWN"},
        "certified_outcomes": {"status": "AVAILABLE", "total": 0, "claimed_records": 0, "latest_decision_at": None, "by_outcome": {"NO_TRADE": 0, "RISK_BLOCKED": 0, "FILLED": 0}, "risk_blocked_reasons": {key: 0 for key in RISK_BLOCK_REASONS}},
    }


def test_m2_source_sanitization_and_acceptance_keep_history_separate_from_eligibility(tmp_path):
    data = {"schema_version": 1, "source": "hope_postgresql", "generated_at": datetime.now(timezone.utc).isoformat(),
            "research": {"run_count": 0}, "paper": {"environment_state": "HALTED", "job_runs": {"total": 0, "claimed": 0, "failed": 0, "succeeded": 0}}, "projections": projection()}
    data["projections"]["strategy_catalog"]["rows"][0]["rationale"] = "NEVER_EXPOSE"
    data["projections"]["execution_ledger"]["raw_fills"] = ["NEVER_EXPOSE"]
    data["projections"]["certified_chain"]["database_principal"] = "NEVER_EXPOSE"
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(data))
    raw, _ = _verified_snapshot(path)
    assert b"NEVER_EXPOSE" not in raw
    safe = json.loads(raw)
    assert safe["projections"] == projection()
    assert safe["projections"]["certified_chain"]["eligibility_policy"] == "UNKNOWN"
    _validate_paper(safe)
    with pytest.raises(ValueError):
        _validate_paper(data)


@pytest.mark.parametrize("section,change", [
    ("strategy_catalog", {"total": 21, "truncated": False}),
    ("strategy_catalog", {"rows": []}),
    ("dataset_certification", {"sealed_versions": 4}),
    ("dataset_certification", {"pit_certified_versions": 3}),
    ("execution_ledger", {"fills": 3}),
    ("execution_ledger", {"applications_without_pnl": 2}),
    ("blocked_runs", {"total": 2}),
    ("decision_activity", {"claimed_no_trade": 1}),
    ("decision_activity", {"latest_decision_at": "2026-01-01T00:00:00Z"}),
    ("certified_chain", {"eligibility_policy": "ELIGIBLE"}),
    ("certified_chain", {"authorization_receipts": 1}),
    ("certified_chain", {"operational_classifications": True}),
    ("certified_outcomes", {"total": 1}),
    ("certified_outcomes", {"claimed_records": 1}),
    ("certified_outcomes", {"by_outcome": {"FILLED": True}}),
    ("certified_outcomes", {"risk_blocked_reasons": {"OTHER": True}}),
])
def test_m2_corruption_or_inferred_proof_is_rejected(section, change):
    value = projection()
    value[section].update(deepcopy(change))
    with pytest.raises(ValueError):
        verified_projections(value, datetime.now(timezone.utc))


@pytest.mark.parametrize("change", [
    {"family_code": "PRIVATE_RATIONALE"}, {"candidate_state": "AUTHORIZED"},
    {"strategy_version_id": "private"}, {"markets": ["USA", "USA"]},
    {"markets": ["USA", "INDIA"]}, {"markets": [{"secret": "private"}]},
])
def test_catalog_row_vocabulary_and_identity_are_bounded(change):
    value = projection()
    value["strategy_catalog"]["rows"][0].update(change)
    with pytest.raises(ValueError):
        verified_projections(value, datetime.now(timezone.utc))


def test_original_projection_vocabulary_remains_compatible():
    old = {name: {"status": "UNAVAILABLE"} for name in SECTIONS}
    assert verified_projections(old, datetime.now(timezone.utc)) == old
