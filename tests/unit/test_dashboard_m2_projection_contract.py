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
        "execution_ledger": {"status": "AVAILABLE", "fills": 2, "applied_fills": 1, "fill_applications": 1, "accounting_receipts": 0, "fills_without_application": 1, "applications_without_pnl": 1, "effect_receipt_fills": 1, "fills_without_effect_receipt": 1, "unattributed_effect_receipt_fills": 0, "effect_receipt_fills_without_application": 0, "claimed_effect_receipt_fills": 0, "succeeded_effect_receipt_fills": 1, "failed_effect_receipt_fills": 0, "claimed_effect_receipt_fills_without_application": 0, "succeeded_effect_receipt_fills_without_application": 0, "failed_effect_receipt_fills_without_application": 0, "accounting_receipts_without_application": 0, "fills_by_market": {"INDIA": 1, "USA": 1, "OTHER": 0}, "unapplied_fills_by_market": {"INDIA": 0, "USA": 1, "OTHER": 0}, "effect_receipt_market_mismatch_fills": 0, "reconciliation_state": "MISSING_EFFECT_RECEIPTS"},
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


def test_v1_last_good_hides_legacy_ledger_instead_of_inventing_effect_receipts(tmp_path):
    value = projection()
    for key in ("effect_receipt_fills", "fills_without_effect_receipt",
                "unattributed_effect_receipt_fills",
                "effect_receipt_fills_without_application",
                "claimed_effect_receipt_fills",
                "succeeded_effect_receipt_fills",
                "failed_effect_receipt_fills",
                "claimed_effect_receipt_fills_without_application",
                "succeeded_effect_receipt_fills_without_application",
                "failed_effect_receipt_fills_without_application",
                "accounting_receipts_without_application", "fills_by_market",
                "unapplied_fills_by_market", "effect_receipt_market_mismatch_fills"):
        value["execution_ledger"].pop(key)
    data = {"schema_version":1, "source":"hope_postgresql",
            "generated_at":datetime.now(timezone.utc).isoformat(),
            "research":{"run_count":0}, "paper":{"environment_state":"RUNNING",
            "job_runs":{"total":0,"claimed":0,"succeeded":0,"failed":0}},
            "projections":value}
    path=tmp_path/"snapshot.json"; path.write_text(json.dumps(data))
    safe=json.loads(_verified_snapshot(path)[0])
    assert safe["projections"]["execution_ledger"]=={"status":"UNAVAILABLE", "reason":"LEGACY_EXECUTION_LEDGER_SCHEMA_INCOMPATIBLE"}
    assert safe["projections"]["certified_chain"]["status"]=="AVAILABLE"


@pytest.mark.parametrize("section,change", [
    ("strategy_catalog", {"total": 21, "truncated": False}),
    ("strategy_catalog", {"rows": []}),
    ("dataset_certification", {"sealed_versions": 4}),
    ("dataset_certification", {"pit_certified_versions": 3}),
    ("execution_ledger", {"fills": 3}),
    ("execution_ledger", {"applications_without_pnl": 2}),
    ("execution_ledger", {"fills_without_effect_receipt": 2}),
    ("execution_ledger", {"unattributed_effect_receipt_fills": 2}),
    ("execution_ledger", {"effect_receipt_fills_without_application": 2}),
    ("execution_ledger", {"failed_effect_receipt_fills": 1}),
    ("execution_ledger", {"succeeded_effect_receipt_fills_without_application": 1}),
    ("execution_ledger", {"fills_by_market": {"INDIA": 2, "USA": 1, "OTHER": 0}}),
    ("execution_ledger", {"unapplied_fills_by_market": {"INDIA": 0, "USA": 0, "OTHER": 0}}),
    ("execution_ledger", {"effect_receipt_market_mismatch_fills": 2}),
    ("execution_ledger", {"reconciliation_state": "OBSERVED_COMPLETE"}),
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


@pytest.mark.parametrize("changes,expected", [
    ({"fills":0,"applied_fills":0,"fill_applications":0,"accounting_receipts":0,
      "fills_without_application":0,"applications_without_pnl":0,
      "effect_receipt_fills":0,"fills_without_effect_receipt":0,
      "unattributed_effect_receipt_fills":0,
      "effect_receipt_fills_without_application":0,
      "succeeded_effect_receipt_fills":0,
      "fills_by_market":{"INDIA":0,"USA":0,"OTHER":0},
      "unapplied_fills_by_market":{"INDIA":0,"USA":0,"OTHER":0}}, "NO_FILLS"),
    ({"accounting_receipts":1,"accounting_receipts_without_application":1}, "ORPHAN_ACCOUNTING_RECEIPTS"),
    ({}, "MISSING_EFFECT_RECEIPTS"),
    ({"unattributed_effect_receipt_fills":1}, "UNATTRIBUTED_EFFECT_RECEIPTS"),
    ({"fills_without_effect_receipt":0,"effect_receipt_fills":2,
      "claimed_effect_receipt_fills":1,"succeeded_effect_receipt_fills":1,
      "effect_receipt_fills_without_application":1,
      "claimed_effect_receipt_fills_without_application":1}, "CLAIMED_EFFECT_RECEIPTS_NOT_APPLIED"),
    ({"fills_without_effect_receipt":0,"effect_receipt_fills":2,
      "succeeded_effect_receipt_fills":2,
      "effect_receipt_fills_without_application":1,
      "succeeded_effect_receipt_fills_without_application":1}, "SUCCEEDED_EFFECT_RECEIPTS_NOT_APPLIED"),
    ({"fills_without_effect_receipt":0,"effect_receipt_fills":2,
      "failed_effect_receipt_fills":1,"succeeded_effect_receipt_fills":1,
      "effect_receipt_fills_without_application":1,
      "failed_effect_receipt_fills_without_application":1}, "FAILED_EFFECT_RECEIPTS_NOT_APPLIED"),
    ({"applied_fills":2,"fill_applications":2,"fills_without_application":0,
      "unapplied_fills_by_market":{"INDIA":0,"USA":0,"OTHER":0},
      "fills_without_effect_receipt":0,"effect_receipt_fills":2,
      "succeeded_effect_receipt_fills":2,
      "effect_receipt_fills_without_application":0,"applications_without_pnl":2}, "APPLICATIONS_WITHOUT_ACCOUNTING_RECEIPTS"),
    ({"applied_fills":2,"fill_applications":2,"fills_without_application":0,
      "unapplied_fills_by_market":{"INDIA":0,"USA":0,"OTHER":0},
      "fills_without_effect_receipt":0,"effect_receipt_fills":2,
      "succeeded_effect_receipt_fills":2,
      "effect_receipt_fills_without_application":0,"applications_without_pnl":0,
      "accounting_receipts":2}, "OBSERVED_COMPLETE"),
    ({"effect_receipt_market_mismatch_fills":1}, "EFFECT_RECEIPT_MARKET_MISMATCH"),
    ({"fills_by_market":{"INDIA":1,"USA":0,"OTHER":1},
      "unapplied_fills_by_market":{"INDIA":0,"USA":0,"OTHER":1}}, "FILL_MARKET_UNRESOLVED"),
])
def test_ledger_reconciliation_state_is_derived_from_aggregate_evidence(changes, expected):
    value = projection()
    ledger = value["execution_ledger"]
    ledger.pop("reconciliation_state")
    ledger.update(changes)
    assert verified_projections(value, datetime.now(timezone.utc))["execution_ledger"]["reconciliation_state"] == expected


MARKET_PAIRS = [(a, b) for a in ("INDIA", "USA", "OTHER")
                for b in ("INDIA", "USA", "OTHER") if a != b]


@pytest.mark.parametrize("total_market,missing_market", MARKET_PAIRS)
def test_market_unapplied_counts_cannot_exceed_their_own_fill_set(total_market, missing_market):
    value = projection()
    ledger = value["execution_ledger"]
    ledger.pop("reconciliation_state")
    ledger["fills_by_market"] = {m: 2 * int(m == total_market) for m in ("INDIA", "USA", "OTHER")}
    ledger["unapplied_fills_by_market"] = {m: int(m == missing_market) for m in ("INDIA", "USA", "OTHER")}
    original = deepcopy(value)
    with pytest.raises(ValueError, match="LEDGER_MARKETS_INCONSISTENT"):
        verified_projections(value, datetime.now(timezone.utc))
    assert value == original


@pytest.mark.parametrize("first,second", MARKET_PAIRS)
def test_market_unapplied_positive_partitions_remain_observations(first, second):
    value = projection()
    ledger = value["execution_ledger"]
    ledger.pop("reconciliation_state")
    ledger["fills_by_market"] = {m: int(m in (first, second)) for m in ("INDIA", "USA", "OTHER")}
    ledger["unapplied_fills_by_market"] = {m: int(m == first) for m in ("INDIA", "USA", "OTHER")}
    result = verified_projections(value, datetime.now(timezone.utc))["execution_ledger"]
    assert result["status"] == "AVAILABLE"
    assert result["fills_by_market"] == ledger["fills_by_market"]
    assert result["unapplied_fills_by_market"] == ledger["unapplied_fills_by_market"]
    assert result["reconciliation_state"] == ("FILL_MARKET_UNRESOLVED" if "OTHER" in (first, second)
                                               else "MISSING_EFFECT_RECEIPTS")


@pytest.mark.parametrize("version", [1, 2])
def test_complete_current_ledger_remains_strict_in_both_snapshot_versions(version):
    from hope.api.snapshot_contract import verified_snapshot_payload
    data = {"schema_version":version,"source":"hope_postgresql","generated_at":datetime.now(timezone.utc).isoformat(),
            "research":{"run_count":0},"paper":{"environment_state":"HALTED","job_runs":{"total":0,"claimed":0,"failed":0,"succeeded":0}},"projections":projection()}
    if version == 2:
        data["provenance"]={"data_classification":"PRODUCTION_READ_ONLY","database_identity_sha256":"a"*64,"synthetic_activity":False,"execution_authorized":False,"live_enabled":False}
    safe=json.loads(verified_snapshot_payload(json.dumps(data).encode())[0])
    assert safe["projections"]["execution_ledger"]==projection()["execution_ledger"]
    data["projections"]["execution_ledger"].pop("effect_receipt_fills")
    with pytest.raises(ValueError):verified_snapshot_payload(json.dumps(data).encode())
