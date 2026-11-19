"""Denial-only PostgreSQL transaction continuity contract.

Both observations must originate from an independent verifier's trusted database
channel. This module cannot attest to their origin or enforce a COMMIT barrier.
"""
from __future__ import annotations
from collections.abc import Mapping
from attestor_session_observation import inspect_session_observation, REQUIRED


def inspect_transaction_continuity(before: Mapping[str, object] | None,
                                   at_commit: Mapping[str, object] | None,
                                   expected: Mapping[str, object] | None,
                                   *, authorization_generation: int,
                                   current_generation: int,
                                   revocation_epoch: int,
                                   authorization_epoch: int) -> str:
    """Refuse stale, revoked, or inconsistent pre-COMMIT observations."""
    for value in (authorization_generation, current_generation,
                  revocation_epoch, authorization_epoch):
        if type(value) is not int or value < 0:
            return "DENY_COMMIT_EPOCH_SCHEMA"
    if authorization_generation != current_generation:
        return "DENY_AUTHORIZATION_GENERATION_CHANGED"
    if revocation_epoch > authorization_epoch:
        return "DENY_AUTHORIZATION_REVOKED"
    if not isinstance(before, Mapping) or not isinstance(at_commit, Mapping) or not isinstance(expected, Mapping):
        return "DENY_COMMIT_OBSERVATION_UNAVAILABLE"
    for observation in (before, at_commit):
        result = inspect_session_observation(observation, expected)
        if result != "DENY_INDEPENDENT_SESSION_CUSTODY_AND_COMMIT_AUTHORITY_UNAVAILABLE":
            return result
    if set(before) != REQUIRED or set(at_commit) != REQUIRED:
        return "DENY_COMMIT_OBSERVATION_SCHEMA"
    if any(before[field] != at_commit[field] for field in REQUIRED):
        return "DENY_TRANSACTION_IDENTITY_CHANGED"
    return "DENY_ATOMIC_COMMIT_BARRIER_UNAVAILABLE"
