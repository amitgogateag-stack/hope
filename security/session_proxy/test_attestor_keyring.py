"""Signer rotation and revocation denial tests (not an admission implementation)."""
import json
import unittest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from attestor_binding import IDENTITY
from attestor_keyring import inspect_keyed_evidence


class KeyringTests(unittest.TestCase):
    def setUp(self):
        self.signer = Ed25519PrivateKey.generate()
        self.other = Ed25519PrivateKey.generate()
        self.key = self.signer.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
        self.other_key = self.other.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
        self.claims = {"version": 1, "audience": "hope-paper-lab",
                       "release_sha256": "a" * 64, "controller_generation": "generation-123456",
                       "session_challenge": "challenge-123456", "postgres_backend": "backend-12345678",
                       "transaction_xid": "xid-123456789012", "policy_generation": "policy-123456789",
                       "issued_at": 100, "expires_at": 200}
        self.payload = json.dumps(self.claims, sort_keys=True).encode()
        self.signature = self.signer.sign(self.payload)
        self.expected = {key: self.claims[key] for key in IDENTITY}

    def check(self, **kwargs):
        options = {"key_id": "trusted-key-01", "trusted_keys": {"trusted-key-01": self.key},
                   "revoked_key_ids": frozenset(), "expected": self.expected, "now": 150}
        options.update(kwargs)
        return inspect_keyed_evidence(self.payload, self.signature, **options)

    def test_trusted_key_still_denies_admission(self):
        self.assertEqual(self.check(), "DENY_INDEPENDENT_SESSION_AND_COMMIT_ATTESTOR_UNAVAILABLE")

    def test_revoked_signer_denied_even_with_valid_signature(self):
        self.assertEqual(self.check(revoked_key_ids=frozenset({"trusted-key-01"})),
                         "DENY_REVOKED_SIGNER")

    def test_unknown_signer_denied(self):
        self.assertEqual(self.check(key_id="unknown-key-01"), "DENY_UNKNOWN_SIGNER")

    def test_wrong_key_mapping_denied(self):
        self.assertEqual(self.check(trusted_keys={"trusted-key-01": self.other_key}),
                         "DENY_INVALID_SIGNATURE")

    def test_missing_trust_configuration_denied(self):
        self.assertEqual(self.check(trusted_keys=None), "DENY_TRUST_CONFIGURATION_UNAVAILABLE")
        self.assertEqual(self.check(revoked_key_ids=None), "DENY_TRUST_CONFIGURATION_UNAVAILABLE")

    def test_malformed_key_id_denied(self):
        for value in ("short", "../trusted-key-01", "x" * 65, ""):
            with self.subTest(value=value):
                self.assertEqual(self.check(key_id=value), "DENY_KEY_ID")

    def test_rotated_signer_requires_verifier_configured_key(self):
        rotated = self.other.sign(self.payload)
        self.assertEqual(inspect_keyed_evidence(
            self.payload, rotated, key_id="rotated-key-02",
            trusted_keys={"trusted-key-01": self.key, "rotated-key-02": self.other_key},
            revoked_key_ids=frozenset({"trusted-key-01"}), expected=self.expected, now=150),
            "DENY_INDEPENDENT_SESSION_AND_COMMIT_ATTESTOR_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
