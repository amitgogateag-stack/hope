"""Adversarial denial-only envelope contract tests."""
import json
import unittest
from attestor_envelope import inspect_envelope


def sample():
    return dict(version=1, audience="hope-paper-lab", release_sha256="a" * 64,
                controller_generation="generation-123456", session_challenge="challenge-123456",
                postgres_backend="backend-12345678", transaction_xid="transaction-123456",
                policy_generation="policy-generation-123", issued_at=1000, expires_at=1100,
                signature="untrusted-signature-12345")


class EnvelopeTests(unittest.TestCase):
    def reason(self, packet, now=1050):
        return inspect_envelope(json.dumps(packet).encode(), audience="hope-paper-lab", now=now)

    def test_valid_shape_still_denied(self):
        self.assertEqual(self.reason(sample()), "DENY_INDEPENDENT_ATTESTOR_UNAVAILABLE")

    def test_wrong_audience(self):
        p = sample(); p["audience"] = "wrong"
        self.assertEqual(self.reason(p), "DENY_AUDIENCE")

    def test_expired_future_and_overlong(self):
        self.assertEqual(self.reason(sample(), 1100), "DENY_EXPIRY")
        self.assertEqual(self.reason(sample(), 999), "DENY_EXPIRY")
        p = sample(); p["expires_at"] = 1400
        self.assertEqual(self.reason(p), "DENY_EXPIRY")

    def test_invalid_clock_boundaries_fail_closed(self):
        for issued, expires, now in (
            (1000, 1000, 1000),
            (1100, 1000, 1050),
            (-1, 100, 50),
            (0, 100, -1),
        ):
            with self.subTest(issued=issued, expires=expires, now=now):
                p = sample()
                p["issued_at"], p["expires_at"] = issued, expires
                self.assertEqual(self.reason(p, now=now), "DENY_CLOCK")

    def test_schema_rejects_missing_extra_boolean_version(self):
        p = sample(); del p["signature"]
        self.assertEqual(self.reason(p), "DENY_SCHEMA")
        p = sample(); p["extra"] = 1
        self.assertEqual(self.reason(p), "DENY_SCHEMA")
        p = sample(); p["version"] = True
        self.assertEqual(self.reason(p), "DENY_SCHEMA")

    def test_digest_identity_and_clock(self):
        p = sample(); p["release_sha256"] = "z" * 64
        self.assertEqual(self.reason(p), "DENY_DIGEST")
        p = sample(); p["transaction_xid"] = ""
        self.assertEqual(self.reason(p), "DENY_IDENTITY")
        p = sample(); p["expires_at"] = True
        self.assertEqual(self.reason(p), "DENY_CLOCK")

    def test_deeply_nested_json_fails_closed(self):
        payload = (b"[" * 1200) + b"0" + (b"]" * 1200)
        self.assertIn(inspect_envelope(payload, audience="hope-paper-lab", now=1050),
                      {"DENY_MALFORMED", "DENY_SCHEMA"})

    def test_duplicate_keys_invalid_encoding_and_size(self):
        self.assertEqual(inspect_envelope(b'{"version":1,"version":1}', audience="hope-paper-lab", now=1050), "DENY_MALFORMED")
        self.assertEqual(inspect_envelope(bytes([255]), audience="hope-paper-lab", now=1050), "DENY_MALFORMED")
        self.assertEqual(inspect_envelope(b"x" * 4097, audience="hope-paper-lab", now=1050), "DENY_INVALID_SIZE")


if __name__ == "__main__":
    unittest.main()
