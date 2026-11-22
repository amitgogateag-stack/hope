"""Isolated verifier-owned replay ledger. Not a production admission service.

The ledger is intentionally in-memory: it cannot provide crash-safe replay
prevention, cross-process coordination, or COMMIT-time authority.
"""
from __future__ import annotations

import threading


class ReplayLedger:
    """Atomically record a session challenge once within one verifier process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seen: set[tuple[str, str]] = set()

    def inspect_once(self, *, signer_id: str, challenge: str, verified_claims: bool) -> str:
        # Caller must first verify signature, issuer, scope, session, and expiry.
        # A caller-provided boolean is not an independent proof of authenticity.
        if not verified_claims:
            return "DENY_UNVERIFIED_CLAIMS"
        if not isinstance(signer_id, str) or not isinstance(challenge, str) or not signer_id or not challenge:
            return "DENY_REPLAY_IDENTITY"
        with self._lock:
            key = (signer_id, challenge)
            if key in self._seen:
                return "DENY_REPLAY"
            self._seen.add(key)
        return "DENY_DURABLE_REPLAY_AND_COMMIT_COORDINATION_UNAVAILABLE"
