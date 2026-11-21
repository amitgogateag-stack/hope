"""Fail-closed laboratory envelope parser; never grants production authority."""
import json
import re

FIELDS = {"version", "audience", "release_sha256", "controller_generation",
          "session_challenge", "postgres_backend", "transaction_xid",
          "policy_generation", "issued_at", "expires_at", "signature"}
HEX = re.compile(r"[0-9a-f]{64}\Z")
TOKEN = re.compile(r"[A-Za-z0-9_-]{16,128}\Z")


def inspect_envelope(raw: bytes, *, audience: str, now: int) -> str:
    """Return denial reason only. Claims are not independently authenticated."""
    if not isinstance(raw, bytes) or not raw or len(raw) > 4096:
        return "DENY_INVALID_SIZE"
    try:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError, TypeError, RecursionError):
        return "DENY_MALFORMED"
    if not isinstance(data, dict) or set(data) != FIELDS or type(data["version"]) is not int or data["version"] != 1:
        return "DENY_SCHEMA"
    if not isinstance(audience, str) or not audience or data["audience"] != audience:
        return "DENY_AUDIENCE"
    if not isinstance(data["release_sha256"], str) or not HEX.fullmatch(data["release_sha256"]):
        return "DENY_DIGEST"
    for key in ("controller_generation", "session_challenge", "postgres_backend",
                "transaction_xid", "policy_generation", "signature"):
        if not isinstance(data[key], str) or not TOKEN.fullmatch(data[key]):
            return "DENY_IDENTITY"
    if type(now) is not int or any(type(data[k]) is not int for k in ("issued_at", "expires_at")):
        return "DENY_CLOCK"
    if min(now, data["issued_at"], data["expires_at"]) < 0 or data["issued_at"] >= data["expires_at"]:
        return "DENY_CLOCK"
    if data["issued_at"] > now or now >= data["expires_at"] or data["expires_at"] - data["issued_at"] > 300:
        return "DENY_EXPIRY"
    # Even a perfectly formed envelope cannot prove custody, peer identity,
    # revocation or COMMIT-coordinated authority.
    return "DENY_INDEPENDENT_ATTESTOR_UNAVAILABLE"
