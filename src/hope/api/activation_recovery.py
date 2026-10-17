"""Read-only interrupted activation diagnosis; never removes a lease or starts services."""
from __future__ import annotations

import json
from pathlib import Path

from hope.api.operations_deployment import DeploymentError, inspect_activation_recovery

ROOT = Path("/opt/hope-operations")


def main() -> int:
    try:
        result = inspect_activation_recovery(ROOT)
    except (DeploymentError, OSError):
        print(json.dumps({"schema_version": 1, "state": "UNTRUSTED", "recovery_safe": False}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["state"] == "ABSENT" or result["recovery_safe"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
