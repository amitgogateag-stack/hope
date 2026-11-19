"""Verifier-owned key registry prototype; cryptographic checks never grant admission.

Registry contents must come from a protected supervisor configuration. Neither
the signer nor the untrusted worker may supply or modify trusted keys.
"""
from __future__ import annotations

from collections.abc import Mapping
from attestor_binding import inspect_bound_evidence

KEY_ID_MIN = 8
KEY_ID_MAX = 64


def inspect_keyed_evidence(payload: bytes, signature: bytes, *, key_id: str,
                           trusted_keys: Mapping[str, bytes] | None,
                           revoked_key_ids: frozenset[str] | None,
                           expected: Mapping[str, object], now: int) -> str:
    """Deny unknown/revoked keys and delegate to the denial-only binding check."""
    if not isinstance(key_id, str) or not KEY_ID_MIN <= len(key_id) <= KEY_ID_MAX or not key_id.isascii() or not all(c.isalnum() or c in "_-" for c in key_id):
        return "DENY_KEY_ID"
    if not isinstance(trusted_keys, Mapping) or not isinstance(revoked_key_ids, frozenset):
        return "DENY_TRUST_CONFIGURATION_UNAVAILABLE"
    if key_id in revoked_key_ids:
        return "DENY_REVOKED_SIGNER"
    key = trusted_keys.get(key_id)
    if not isinstance(key, bytes) or len(key) != 32:
        return "DENY_UNKNOWN_SIGNER"
    return inspect_bound_evidence(payload, signature, trusted_public_key=key,
                                  expected=expected, now=now)
