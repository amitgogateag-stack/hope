"""Cryptographic evidence checks are not production admission checks."""
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from attestor_signature import inspect_signed_evidence


class SignedEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.private = Ed25519PrivateKey.generate()
        self.public = self.private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.payload = b'{"audience":"hope-paper-lab","generation":"generation-123"}'
        self.signature = self.private.sign(self.payload)

    def inspect(self, payload=None, signature=None, key=None):
        return inspect_signed_evidence(
            self.payload if payload is None else payload,
            self.signature if signature is None else signature,
            trusted_public_key=self.public if key is None else key,
        )

    def test_valid_signature_still_cannot_authorize(self):
        self.assertEqual(self.inspect(), "DENY_SESSION_AND_COMMIT_BINDING_UNAVAILABLE")

    def test_modified_claims_denied(self):
        self.assertEqual(self.inspect(payload=self.payload + b" "), "DENY_INVALID_SIGNATURE")

    def test_wrong_signer_denied(self):
        other = Ed25519PrivateKey.generate().public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.assertEqual(self.inspect(key=other), "DENY_INVALID_SIGNATURE")

    def test_missing_root_denied(self):
        self.assertEqual(self.inspect(key=b""), "DENY_TRUST_ROOT_UNAVAILABLE")

    def test_truncated_signature_denied(self):
        self.assertEqual(self.inspect(signature=self.signature[:-1]), "DENY_INVALID_SIGNATURE")

    def test_empty_and_oversized_payload_denied(self):
        self.assertEqual(self.inspect(payload=b""), "DENY_INVALID_SIZE")
        self.assertEqual(self.inspect(payload=b"x" * 4097), "DENY_INVALID_SIZE")

    def test_non_bytes_input_denied(self):
        self.assertEqual(inspect_signed_evidence("text", self.signature, trusted_public_key=self.public),
                         "DENY_INVALID_SIZE")
        self.assertEqual(inspect_signed_evidence(self.payload, "signature", trusted_public_key=self.public),
                         "DENY_INVALID_SIGNATURE")


if __name__ == "__main__":
    unittest.main()
