"""Explicit v1 aggregate compatibility never supplies missing financial evidence."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import pytest
from hope.api.snapshot_contract import verified_snapshot_payload

REASON = "LEGACY_EXECUTION_LEDGER_SCHEMA_INCOMPATIBLE"

def legacy():
    return {"schema_version":1,"source":"hope_postgresql","generated_at":"2026-01-01T00:00:00+00:00",
        "research":{"run_count":7},"paper":{"environment_state":"HALTED","job_runs":{"total":4,"claimed":1,"succeeded":2,"failed":1}},
        "projections":{"datasets":{"status":"AVAILABLE","registered":3,"pit_certified":1},
        "execution_ledger":{"status":"AVAILABLE","fills":3,"applied_fills":1,"fill_applications":1,
        "accounting_receipts":1,"fills_without_application":2,"applications_without_pnl":0}}}

def verify(value):
    return json.loads(verified_snapshot_payload(json.dumps(value).encode(), now=datetime(2026,1,2,tzinfo=timezone.utc))[0])

def test_legacy_keeps_independent_observations_and_names_missing_evidence():
    value=legacy(); original=deepcopy(value); safe=verify(value)
    assert safe["research"]==value["research"]
    assert safe["paper"]==value["paper"]
    assert safe["projections"]["datasets"]==value["projections"]["datasets"]
    assert safe["projections"]["execution_ledger"]=={"status":"UNAVAILABLE","reason":REASON}
    assert value==original
    assert verify(safe)==safe

@pytest.mark.parametrize("change", [{"fills":4},{"fills":True},{"fills":-1},{"applied_fills":2},
    {"applications_without_pnl":2},{"accounting_receipts":"1"},{"fill_applications":0},
    {"effect_receipt_fills":99},{"fills_by_market":{"USA":99}}, {"reconciliation_state":"PRIVATE"}, {"reconciliation_state":{}}])
def test_incomplete_legacy_cannot_hide_present_corruption(change):
    value=legacy();value["projections"]["execution_ledger"].update(change)
    with pytest.raises(ValueError):verify(value)

def test_modern_schema_never_uses_legacy_fallback():
    value=legacy();value["schema_version"]=2
    value["provenance"]={"data_classification":"PRODUCTION_READ_ONLY","database_identity_sha256":"a"*64,
        "synthetic_activity":False,"execution_authorized":False,"live_enabled":False}
    with pytest.raises(ValueError):verify(value)

@pytest.mark.parametrize("field", ["execution_authorized","live_enabled"])
def test_legacy_rejects_unsafe_provenance_instead_of_ignoring_it(field):
    value=legacy();value["provenance"]={field:True}
    with pytest.raises(ValueError):verify(value)

def test_legacy_rejects_invalid_unavailable_reason():
    value=legacy();value["projections"]["execution_ledger"]={"status":"UNAVAILABLE","reason":"PRIVATE"}
    with pytest.raises(ValueError):verify(value)
