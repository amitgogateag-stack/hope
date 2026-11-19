"""Crash and concurrency tests for denial-only persistent replay prototype."""
import concurrent.futures
import tempfile
import unittest
from pathlib import Path

from attestor_durable_replay import DurableReplayLedger


class DurableReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "replay.sqlite3"
        self.ledger = DurableReplayLedger(self.path)
        self.ledger.initialize()

    def check(self, ledger=None, **overrides):
        options = {"signer_id": "trusted-key-01", "challenge": "nonce-123456789",
                   "verified_claims": True}
        options.update(overrides)
        return (ledger or self.ledger).inspect_once(**options)

    def test_first_observation_still_denies(self):
        self.assertEqual(self.check(), "DENY_INDEPENDENT_SESSION_AND_COMMIT_AUTHORITY_UNAVAILABLE")

    def test_duplicate_denied(self):
        self.check()
        self.assertEqual(self.check(), "DENY_REPLAY")

    def test_restart_preserves_replay_denial(self):
        self.check()
        recovered = DurableReplayLedger(self.path)
        recovered.initialize()
        self.assertEqual(self.check(recovered), "DENY_REPLAY")

    def test_unverified_claim_does_not_consume_nonce(self):
        self.assertEqual(self.check(verified_claims=False), "DENY_UNVERIFIED_CLAIMS")
        self.assertEqual(self.check(), "DENY_INDEPENDENT_SESSION_AND_COMMIT_AUTHORITY_UNAVAILABLE")

    def test_concurrent_connections_have_single_first_observation(self):
        def attempt(_):
            return self.check(DurableReplayLedger(self.path))
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            outcomes = list(pool.map(attempt, range(24)))
        self.assertEqual(outcomes.count("DENY_INDEPENDENT_SESSION_AND_COMMIT_AUTHORITY_UNAVAILABLE"), 1)
        self.assertEqual(outcomes.count("DENY_REPLAY"), 23)

    def test_missing_storage_denies(self):
        missing = DurableReplayLedger(Path(self.temp.name) / "absent" / "db.sqlite")
        self.assertEqual(self.check(missing), "DENY_REPLAY_STORAGE_UNAVAILABLE")

    def test_corrupt_storage_denies(self):
        self.path.write_bytes(b"not a database")
        self.assertEqual(self.check(), "DENY_REPLAY_STORAGE_UNAVAILABLE")

    def test_missing_identity_denies(self):
        self.assertEqual(self.check(challenge=""), "DENY_REPLAY_IDENTITY")
        self.assertEqual(self.check(signer_id=""), "DENY_REPLAY_IDENTITY")

    def test_separate_signers_have_independent_nonce_namespaces(self):
        self.check()
        self.assertEqual(self.check(signer_id="trusted-key-02"),
                         "DENY_INDEPENDENT_SESSION_AND_COMMIT_AUTHORITY_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
