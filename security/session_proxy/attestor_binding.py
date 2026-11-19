"""Denial-only signed-claim binding. No production authorization capability."""
from __future__ import annotations
import json
import re
from collections.abc import Mapping
from attestor_signature import inspect_signed_evidence

FIELDS = frozenset({"version", "audience", "release_sha256", "controller_generation",
                    "session_challenge", "postgres_backend", "transaction_xid",
                    "policy_generation", "issued_at", "expires_at"})
IDENTITY = FIELDS - {"version", "issued_at", "expires_at"}
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
TOKEN = re.compile(r"[A-Za-z0-9_-]{16,128}\Z")
MAX_AUDIENCE_BYTES = 128


def inspect_bound_evidence(payload: bytes, signature: bytes, *, trusted_public_key: bytes | None,
                           expected: Mapping[str, object], now: int) -> str:
    """Verify signature and verifier-owned expectations; still deny admission."""
    verdict = inspect_signed_evidence(payload, signature, trusted_public_key=trusted_public_key)
    if verdict != "DENY_SESSION_AND_COMMIT_BINDING_UNAVAILABLE":
        return verdict
    try:
        def unique(pairs):
            obj = {}
            for key, value in pairs:
                if key in obj:
                    raise ValueError("duplicate key")
                obj[key] = value
            return obj
        claims = json.loads(payload.decode("utf-8"), object_pairs_hook=unique,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        return "DENY_MALFORMED_CLAIMS"
    if not isinstance(claims, dict) or set(claims) != FIELDS or type(claims["version"]) is not int or claims["version"] != 1:
        return "DENY_CLAIM_SCHEMA"
    if not isinstance(expected, Mapping) or set(expected) != IDENTITY:
        return "DENY_EXPECTATION_UNAVAILABLE"
    if any(not isinstance(claims[k], str) or not claims[k] or
           not isinstance(expected[k], str) or not expected[k] for k in IDENTITY):
        return "DENY_CLAIM_SCHEMA"
    if (not DIGEST.fullmatch(claims["release_sha256"]) or
        any(not TOKEN.fullmatch(claims[k]) for k in IDENTITY - {"audience", "release_sha256"}) or
        len(claims["audience"].encode("utf-8")) > MAX_AUDIENCE_BYTES):
        return "DENY_CLAIM_SCHEMA"
    if any(claims[k] != expected[k] for k in IDENTITY):
        return "DENY_CLAIM_MISMATCH"
    issued, expires = claims["issued_at"], claims["expires_at"]
    if type(now) is not int or type(issued) is not int or type(expires) is not int or min(now, issued, expires) < 0:
        return "DENY_CLOCK"
    if issued >= expires or expires - issued > 300 or now < issued or now >= expires:
        return "DENY_EXPIRY"
    return "DENY_INDEPENDENT_SESSION_AND_COMMIT_ATTESTOR_UNAVAILABLE"
