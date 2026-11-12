"""Denial evidence is not an admission permit or production certification."""
import pytest

from hope.security.denial_evidence import (
    MISSING_ATTESTOR_PROBE,
    REQUIRED_DENIAL_SQLSTATES,
    verify_denial_evidence,
)


def valid_entries():
    rows = [
        {"name": name, "status": "PASS", "sqlstate": sqlstate}
        for name, sqlstate in REQUIRED_DENIAL_SQLSTATES.items()
    ]
    rows.append({"name": MISSING_ATTESTOR_PROBE, "status": "PASS"})
    return rows


def test_all_twelve_denials_are_required_and_valid():
    assert len(valid_entries()) == 12
    assert verify_denial_evidence(valid_entries()) is None


@pytest.mark.parametrize("mutation", [
    "missing", "duplicate", "unknown", "wrong_sqlstate", "failed",
    "missing_attestor_sqlstate", "extra_field", "not_list",
])
def test_denial_evidence_fails_closed_on_incomplete_or_forged_data(mutation):
    rows = valid_entries()
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows.append(rows[0].copy())
    elif mutation == "unknown":
        rows[0]["name"] = "synthetic_success"
    elif mutation == "wrong_sqlstate":
        rows[0]["sqlstate"] = "42501"
    elif mutation == "failed":
        rows[0]["status"] = "FAIL"
    elif mutation == "missing_attestor_sqlstate":
        rows[-1]["sqlstate"] = "23514"
    elif mutation == "extra_field":
        rows[0]["approval_issued"] = True
    else:
        rows = {"experiments": rows}
    with pytest.raises(ValueError, match="A1_DENIAL_EVIDENCE_"):
        verify_denial_evidence(rows)
