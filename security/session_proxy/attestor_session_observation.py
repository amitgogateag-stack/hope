"""Verifier-side PostgreSQL session observation contract (denial-only).

Observations must be obtained from a trusted database connection owned by the
independent verifier, not supplied by a worker or signed claims. This isolated
validator does not establish such custody or grant transaction authority.
"""
from __future__ import annotations
from collections.abc import Mapping

REQUIRED = frozenset({"backend_pid", "database_oid", "session_user", "transaction_xid",
                      "server_start_epoch", "controller_generation"})


def inspect_session_observation(observed: Mapping[str, object] | None,
                                expected: Mapping[str, object] | None) -> str:
    """Reject absent, malformed, stale or mismatched database session evidence."""
    if not isinstance(observed, Mapping) or not isinstance(expected, Mapping):
        return "DENY_SESSION_OBSERVATION_UNAVAILABLE"
    if set(observed) != REQUIRED or set(expected) != REQUIRED:
        return "DENY_SESSION_OBSERVATION_SCHEMA"
    for field in ("backend_pid", "database_oid", "transaction_xid", "server_start_epoch"):
        if (type(observed[field]) is not int or type(expected[field]) is not int or
                observed[field] <= 0 or expected[field] <= 0):
            return "DENY_SESSION_OBSERVATION_SCHEMA"
    for field in ("session_user", "controller_generation"):
        if (not isinstance(observed[field], str) or not isinstance(expected[field], str) or
                not observed[field] or not expected[field] or
                len(observed[field]) > 128 or len(expected[field]) > 128):
            return "DENY_SESSION_OBSERVATION_SCHEMA"
    if any(observed[field] != expected[field] for field in REQUIRED):
        return "DENY_SESSION_OBSERVATION_MISMATCH"
    return "DENY_INDEPENDENT_SESSION_CUSTODY_AND_COMMIT_AUTHORITY_UNAVAILABLE"
