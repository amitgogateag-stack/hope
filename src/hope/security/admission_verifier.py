"""Standalone fail-closed verifier entry point.

No worker-provided evidence can supply the missing trusted session attestor.
This executable intentionally has no database credential or approval-write path.
"""
from __future__ import annotations

import json
import sys

MAX_REQUEST_BYTES = 16384
MISSING_CAPABILITY = "TRUSTED_KERNEL_TO_DATABASE_SESSION_BINDING_UNAVAILABLE"


def verify_request(request: object) -> dict[str, object]:
    # Never deserialize a Permit, import a worker-selected provider, or accept an
    # attestation boolean. A future adapter requires a separate security review.
    return {
        "protocol": "hope-independent-admission-v1",
        "decision": "DENY",
        "reason": MISSING_CAPABILITY,
        "approval_issued": False,
    }


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    try:
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("request too large")
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise ValueError("object required")
    except (ValueError, UnicodeError):
        result = {"decision": "DENY", "reason": "INVALID_REQUEST", "approval_issued": False}
    else:
        result = verify_request(request)
    print(json.dumps(result, sort_keys=True))
    return 78  # configuration unavailable; must never be interpreted as admission


if __name__ == "__main__":
    raise SystemExit(main())
