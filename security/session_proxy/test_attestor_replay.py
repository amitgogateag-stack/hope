"""Concurrent replay denial checks; no production authorization."""
import threading
import unittest
from attestor_replay import ReplayLedger


class ReplayLedgerTests(unittest.TestCase):
    def test_first_observation_is_still_denied(self):
        ledger = ReplayLedger()
        self.assertEqual(ledger.inspect_once(signer_id="trusted-key", challenge="nonce-123", verified_claims=True),
                         "DENY_DURABLE_REPLAY_AND_COMMIT_COORDINATION_UNAVAILABLE")

    def test_duplicate_challenge_denied(self):
        ledger = ReplayLedger()
        ledger.inspect_once(signer_id="trusted-key", challenge="nonce-123", verified_claims=True)
        self.assertEqual(ledger.inspect_once(signer_id="trusted-key", challenge="nonce-123", verified_claims=True),
                         "DENY_REPLAY")

    def test_unverified_claims_do_not_consume_nonce(self):
        ledger = ReplayLedger()
        self.assertEqual(ledger.inspect_once(signer_id="trusted-key", challenge="nonce-123", verified_claims=False),
                         "DENY_UNVERIFIED_CLAIMS")
        self.assertEqual(ledger.inspect_once(signer_id="trusted-key", challenge="nonce-123", verified_claims=True),
                         "DENY_DURABLE_REPLAY_AND_COMMIT_COORDINATION_UNAVAILABLE")

    def test_missing_identity_denied(self):
        ledger = ReplayLedger()
        self.assertEqual(ledger.inspect_once(signer_id="", challenge="nonce", verified_claims=True),
                         "DENY_REPLAY_IDENTITY")
        self.assertEqual(ledger.inspect_once(signer_id="key", challenge="", verified_claims=True),
                         "DENY_REPLAY_IDENTITY")

    def test_simultaneous_duplicate_consumption(self):
        ledger = ReplayLedger()
        barrier = threading.Barrier(12)
        outcomes = []
        lock = threading.Lock()

        def attempt():
            barrier.wait(timeout=5)
            result = ledger.inspect_once(signer_id="trusted-key", challenge="nonce-123", verified_claims=True)
            with lock:
                outcomes.append(result)

        threads = [threading.Thread(target=attempt) for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(outcomes.count("DENY_REPLAY"), 11)
        self.assertEqual(outcomes.count("DENY_DURABLE_REPLAY_AND_COMMIT_COORDINATION_UNAVAILABLE"), 1)

    def test_restarting_process_loses_replay_state_and_still_denies(self):
        first = ReplayLedger()
        first.inspect_once(signer_id="trusted-key", challenge="nonce-123", verified_claims=True)
        restarted = ReplayLedger()
        self.assertEqual(restarted.inspect_once(signer_id="trusted-key", challenge="nonce-123", verified_claims=True),
                         "DENY_DURABLE_REPLAY_AND_COMMIT_COORDINATION_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
