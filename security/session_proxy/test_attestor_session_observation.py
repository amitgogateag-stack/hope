"""Adversarial tests for verifier-side PostgreSQL session observation."""
import unittest
from attestor_session_observation import REQUIRED, inspect_session_observation


class SessionObservationTests(unittest.TestCase):
    def setUp(self):
        self.expected = {"backend_pid": 11234, "database_oid": 16384,
                         "session_user": "hope_verifier", "transaction_xid": 987654,
                         "server_start_epoch": 1730000000,
                         "controller_generation": "controller-generation-123"}

    def check(self, observed=None, expected=None):
        return inspect_session_observation(
            self.expected if observed is None else observed,
            self.expected if expected is None else expected)

    def test_matching_observation_still_denied(self):
        self.assertEqual(self.check(), "DENY_INDEPENDENT_SESSION_CUSTODY_AND_COMMIT_AUTHORITY_UNAVAILABLE")

    def test_each_mismatched_identity_denied(self):
        for field in REQUIRED:
            altered = dict(self.expected)
            altered[field] = altered[field] + 1 if type(altered[field]) is int else altered[field] + "-forged"
            with self.subTest(field=field):
                self.assertEqual(self.check(altered), "DENY_SESSION_OBSERVATION_MISMATCH")

    def test_missing_observation_denied(self):
        self.assertEqual(inspect_session_observation(None, self.expected),
                         "DENY_SESSION_OBSERVATION_UNAVAILABLE")
        self.assertEqual(inspect_session_observation(self.expected, None),
                         "DENY_SESSION_OBSERVATION_UNAVAILABLE")

    def test_unknown_or_missing_fields_denied(self):
        self.assertEqual(self.check(dict(self.expected, worker_claim="forged")),
                         "DENY_SESSION_OBSERVATION_SCHEMA")
        missing = dict(self.expected)
        del missing["transaction_xid"]
        self.assertEqual(self.check(missing), "DENY_SESSION_OBSERVATION_SCHEMA")

    def test_boolean_and_zero_identifiers_denied(self):
        for value in (True, False, 0, -1, "123"):
            with self.subTest(value=value):
                self.assertEqual(self.check(dict(self.expected, backend_pid=value)),
                                 "DENY_SESSION_OBSERVATION_SCHEMA")

    def test_missing_role_and_generation_denied(self):
        for field in ("session_user", "controller_generation"):
            with self.subTest(field=field):
                self.assertEqual(self.check(dict(self.expected, **{field: ""})),
                                 "DENY_SESSION_OBSERVATION_SCHEMA")


if __name__ == "__main__":
    unittest.main()
