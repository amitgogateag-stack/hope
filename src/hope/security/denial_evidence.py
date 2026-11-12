"""Strict accounting for deny-only A1 containment evidence.

Never promotes containment to independent admission acceptance.
"""
from __future__ import annotations

REQUIRED_DENIAL_SQLSTATES = {
    "self_classify": "23514",
    "self_approve": "23514",
    "self_superuser": "42501",
    "impersonate_verifier": "42501",
    "impersonate_owner": "42501",
    "grant_self_verifier_role": "42501",
    "create_shadow_attestor": "42501",
    "direct_financial_insert": "23514",
    "verifier_cannot_approve": "42501",
    "verifier_cannot_fund": "42501",
    "verifier_cannot_self_classify": "42501",
}
MISSING_ATTESTOR_PROBE = "missing_trusted_attestor_denied"


def verify_denial_evidence(experiments: object) -> None:
    """Refuse absent, duplicate, unexpected, or misclassified negative probes."""
    if not isinstance(experiments, list):
        raise ValueError("A1_DENIAL_EVIDENCE_INVALID")
    required = set(REQUIRED_DENIAL_SQLSTATES) | {MISSING_ATTESTOR_PROBE}
    seen: set[str] = set()
    for entry in experiments:
        if not isinstance(entry, dict):
            raise ValueError("A1_DENIAL_EVIDENCE_INVALID")
        name = entry.get("name")
        if not isinstance(name, str) or name not in required or name in seen:
            raise ValueError("A1_DENIAL_EVIDENCE_INVALID")
        seen.add(name)
        if entry.get("status") != "PASS":
            raise ValueError("A1_DENIAL_EVIDENCE_INVALID")
        if name == MISSING_ATTESTOR_PROBE:
            if set(entry) != {"name", "status"}:
                raise ValueError("A1_DENIAL_EVIDENCE_INVALID")
        elif (set(entry) != {"name", "status", "sqlstate"}
                or entry["sqlstate"] != REQUIRED_DENIAL_SQLSTATES[name]):
            raise ValueError("A1_DENIAL_EVIDENCE_INVALID")
    if seen != required:
        raise ValueError("A1_DENIAL_EVIDENCE_INCOMPLETE")
