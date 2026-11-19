"""Adversarial signed claim-binding checks; never authorize PAPER admission."""
import json
import unittest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from attestor_binding import IDENTITY, inspect_bound_evidence


class BoundEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.private = Ed25519PrivateKey.generate()
        self.public = self.private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
        self.claims = {"version": 1, "audience": "hope-paper-lab", "release_sha256": "a" * 64,
                       "controller_generation": "generation-123456", "session_challenge": "challenge-123456",
                       "postgres_backend": "backend-12345678", "transaction_xid": "xid-123456789012",
                       "policy_generation": "policy-123456789", "issued_at": 100, "expires_at": 200}
        self.expected = {key: self.claims[key] for key in IDENTITY}

    def check(self, claims=None, *, expected=None, now=150, payload=None, signature=None, key=None):
        raw = payload if payload is not None else json.dumps(claims if claims is not None else self.claims, sort_keys=True).encode()
        sig = signature if signature is not None else self.private.sign(raw)
        return inspect_bound_evidence(raw, sig, trusted_public_key=self.public if key is None else key,
                                      expected=self.expected if expected is None else expected, now=now)

    def test_matching_signature_and_claims_never_authorize(self):
        self.assertEqual(self.check(), "DENY_INDEPENDENT_SESSION_AND_COMMIT_ATTESTOR_UNAVAILABLE")

    def test_each_identity_mismatch_denied(self):
        for field in IDENTITY:
            with self.subTest(field=field):
                self.assertEqual(self.check(dict(self.claims, **{field: "wrong-value"})), "DENY_CLAIM_MISMATCH")

    def test_signed_malformed_digest_denied(self):
        self.assertEqual(self.check(dict(self.claims, release_sha256="not-a-digest")),
                         "DENY_CLAIM_SCHEMA")

    def test_signed_short_process_identity_denied(self):
        self.assertEqual(self.check(dict(self.claims, controller_generation="short")),
                         "DENY_CLAIM_SCHEMA")

    def test_signed_oversized_audience_denied(self):
        self.assertEqual(self.check(dict(self.claims, audience="x" * 129)),
                         "DENY_CLAIM_SCHEMA")

    def test_forged_signature_denied(self):
        self.assertEqual(self.check(signature=b"0" * 64), "DENY_INVALID_SIGNATURE")

    def test_missing_trust_root_denied(self):
        self.assertEqual(self.check(key=b""), "DENY_TRUST_ROOT_UNAVAILABLE")

    def test_missing_verifier_expectation_denied(self):
        self.assertEqual(self.check(expected={}), "DENY_EXPECTATION_UNAVAILABLE")

    def test_replayed_and_expired_window_denied(self):
        for now in (99, 200, 201):
            with self.subTest(now=now):
                self.assertEqual(self.check(now=now), "DENY_EXPIRY")

    def test_inverted_and_long_windows_denied(self):
        for start, end in ((200, 100), (100, 401)):
            with self.subTest(start=start, end=end):
                self.assertEqual(self.check(dict(self.claims, issued_at=start, expires_at=end)), "DENY_EXPIRY")

    def test_boolean_clock_denied(self):
        self.assertEqual(self.check(now=True), "DENY_CLOCK")
        self.assertEqual(self.check(dict(self.claims, issued_at=True)), "DENY_CLOCK")

    def test_duplicate_keys_denied(self):
        self.assertEqual(self.check(payload=b'{"version":1,"version":1}'), "DENY_MALFORMED_CLAIMS")

    def test_unknown_or_missing_claim_denied(self):
        self.assertEqual(self.check(dict(self.claims, unexpected="x")), "DENY_CLAIM_SCHEMA")
        short = dict(self.claims)
        del short["audience"]
        self.assertEqual(self.check(short), "DENY_CLAIM_SCHEMA")


if __name__ == "__main__":
    unittest.main()
