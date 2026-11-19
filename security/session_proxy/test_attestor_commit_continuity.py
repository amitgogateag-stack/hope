"""Adversarial denial checks for pre-COMMIT continuity."""
import unittest
from attestor_commit_continuity import inspect_transaction_continuity


class CommitContinuityTests(unittest.TestCase):
    def setUp(self):
        self.observation = {"backend_pid": 11234, "database_oid": 16384,
                            "session_user": "hope_verifier", "transaction_xid": 987654,
                            "server_start_epoch": 1730000000,
                            "controller_generation": "controller-generation-123"}
        self.options = {"authorization_generation": 8, "current_generation": 8,
                        "revocation_epoch": 10, "authorization_epoch": 10}

    def check(self, before=None, at_commit=None, expected=None, **changes):
        opts = dict(self.options)
        opts.update(changes)
        return inspect_transaction_continuity(
            self.observation if before is None else before,
            self.observation if at_commit is None else at_commit,
            self.observation if expected is None else expected, **opts)

    def test_matching_evidence_never_authorizes_commit(self):
        self.assertEqual(self.check(), "DENY_ATOMIC_COMMIT_BARRIER_UNAVAILABLE")

    def test_generation_changed_denies(self):
        self.assertEqual(self.check(current_generation=9),
                         "DENY_AUTHORIZATION_GENERATION_CHANGED")

    def test_revocation_after_authorization_denies(self):
        self.assertEqual(self.check(revocation_epoch=11),
                         "DENY_AUTHORIZATION_REVOKED")

    def test_bad_epoch_values_deny(self):
        for value in (-1, True, "8", None):
            with self.subTest(value=value):
                self.assertEqual(self.check(current_generation=value),
                                 "DENY_COMMIT_EPOCH_SCHEMA")

    def test_backend_replacement_denies(self):
        changed = dict(self.observation, backend_pid=11235)
        self.assertEqual(self.check(at_commit=changed),
                         "DENY_SESSION_OBSERVATION_MISMATCH")

    def test_transaction_replacement_denies(self):
        changed = dict(self.observation, transaction_xid=987655)
        self.assertEqual(self.check(at_commit=changed),
                         "DENY_SESSION_OBSERVATION_MISMATCH")

    def test_server_restart_denies(self):
        changed = dict(self.observation, server_start_epoch=1730000001)
        self.assertEqual(self.check(at_commit=changed),
                         "DENY_SESSION_OBSERVATION_MISMATCH")

    def test_missing_commit_evidence_denies(self):
        self.assertEqual(inspect_transaction_continuity(
            self.observation, None, self.observation, **self.options),
            "DENY_COMMIT_OBSERVATION_UNAVAILABLE")

    def test_malformed_commit_observation_denies(self):
        changed = dict(self.observation)
        del changed["transaction_xid"]
        self.assertEqual(self.check(at_commit=changed),
                         "DENY_SESSION_OBSERVATION_SCHEMA")


if __name__ == "__main__":
    unittest.main()
