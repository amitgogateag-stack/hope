"""Standalone fail-closed verifier entry point.

No worker-provided evidence can supply the missing trusted session attestor.
This executable intentionally has no database credential or approval-write path.
"""
from __future__ import annotations

import json
import sys

MAX_REQUEST_BYTES = 16384
MAX_REQUEST_DEPTH = 64
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


def _invalid_constant(_value: str) -> object:
    raise ValueError("non-finite JSON constant")


def _validate_depth(value: object) -> None:
    pending = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        if depth > MAX_REQUEST_DEPTH:
            raise ValueError("request too deep")
        children = item.values() if isinstance(item, dict) else item if isinstance(item, list) else ()
        pending.extend((child, depth + 1) for child in children
                       if isinstance(child, (dict, list)))


def verify_request(request: object) -> dict[str, object]:
    # Never deserialize a Permit, import a worker-selected provider, or accept an
    # attestation boolean. A future adapter requires a separate security review.
    return _deny(MISSING_CAPABILITY)


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    try:
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("request too large")
        request = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                             parse_constant=_invalid_constant)
        if not isinstance(request, dict):
            raise ValueError("object required")
        _validate_depth(request)
    except (ValueError, UnicodeError, RecursionError):
        result = _deny(INVALID_REQUEST)
    else:
        result = verify_request(request)
    sys.stdout.buffer.write((json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
    return 78  # configuration unavailable; must never be interpreted as admission


if __name__ == "__main__":
    raise SystemExit(main())
