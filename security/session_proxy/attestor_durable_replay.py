"""Crash-persistent replay-denial laboratory, never an admission authority.

SQLite UNIQUE constraints serialize competing nonce claims on one durable file.
This does not establish protected storage, independent process identity,
distributed coordination, or PostgreSQL COMMIT-time authorization.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path


class DurableReplayLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)

    def initialize(self) -> None:
        with closing(sqlite3.connect(self.path, timeout=5, isolation_level=None)) as conn:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("CREATE TABLE IF NOT EXISTS replay_denials ("
                         "signer_id TEXT NOT NULL, challenge TEXT NOT NULL, "
                         "PRIMARY KEY (signer_id, challenge)) WITHOUT ROWID")

    def inspect_once(self, *, signer_id: str, challenge: str, verified_claims: bool) -> str:
        if verified_claims is not True:
            return "DENY_UNVERIFIED_CLAIMS"
        if (not isinstance(signer_id, str) or not isinstance(challenge, str)
                or not 1 <= len(signer_id) <= 128 or not 1 <= len(challenge) <= 256):
            return "DENY_REPLAY_IDENTITY"
        try:
            signer_id.encode('utf-8', errors='strict')
            challenge.encode('utf-8', errors='strict')
        except UnicodeError:
            return "DENY_REPLAY_IDENTITY"
        try:
            with closing(sqlite3.connect(self.path, timeout=5)) as conn, conn:
                conn.execute("PRAGMA busy_timeout=5000")
                conn.execute("INSERT INTO replay_denials(signer_id, challenge) VALUES (?, ?)",
                             (signer_id, challenge))
        except sqlite3.IntegrityError as exc:
            # Only the expected composite PRIMARY KEY collision is a replay.
            # Other integrity errors can indicate schema corruption/tampering.
            if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY:
                return "DENY_REPLAY"
            return "DENY_REPLAY_STORAGE_UNAVAILABLE"
        except (sqlite3.Error, OSError, ValueError):
            return "DENY_REPLAY_STORAGE_UNAVAILABLE"
        return "DENY_INDEPENDENT_SESSION_AND_COMMIT_AUTHORITY_UNAVAILABLE"
