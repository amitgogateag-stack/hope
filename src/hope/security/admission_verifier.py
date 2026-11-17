"""Standalone fail-closed verifier entry point.

No worker-provided evidence can supply the missing trusted session attestor.
This executable intentionally has no database credential or approval-write path.
"""
from __future__ import annotations

import json
import sys

MAX_REQUEST_BYTES = 16384
PROTOCOL = "hope-independent-admission-v1"
MISSING_CAPABILITY = "TRUSTED_KERNEL_TO_DATABASE_SESSION_BINDING_UNAVAILABLE"
INVALID_REQUEST = "INVALID_REQUEST"


def _deny(reason: str) -> dict[str, object]:
    return {"protocol": PROTOCOL, "decision": "DENY", "reason": reason,
            "approval_issued": False}


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def verify_request(request: object) -> dict[str, object]:
    # Never deserialize a Permit, import a worker-selected provider, or accept an
    # attestation boolean. A future adapter requires a separate security review.
    return _deny(MISSING_CAPABILITY)


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    try:
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("request too large")
        request = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(request, dict):
            raise ValueError("object required")
    except (ValueError, UnicodeError):
        result = _deny(INVALID_REQUEST)
    else:
        result = verify_request(request)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 78  # configuration unavailable; must never be interpreted as admission


if __name__ == "__main__":
    raise SystemExit(main())
