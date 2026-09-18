from __future__ import annotations

import hashlib
from typing import Any

from hope.application.experiments.config_hash import canonical_json


def research_comparison_fingerprint(canonical_comparison: Any) -> str:
    """Fingerprint the exact comparison document judged by a research decision."""
    return hashlib.sha256(
        canonical_json(canonical_comparison).encode("utf-8")
    ).hexdigest()
