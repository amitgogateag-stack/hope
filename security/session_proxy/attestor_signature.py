"""Laboratory-only Ed25519 evidence verifier; never issues production admission.

The trusted public key must be provisioned by an independent supervisor, not
supplied by the untrusted envelope. Signature verification alone cannot prove
process custody, PostgreSQL session binding, or COMMIT-time revocation.
"""
from __future__ import annotations

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

MAX_PAYLOAD = 4096
SIGNATURE_BYTES = 64
PUBLIC_KEY_BYTES = 32


def inspect_signed_evidence(
    payload: bytes, signature: bytes, *, trusted_public_key: bytes | None
) -> str:
    """Verify an externally anchored signature, but always deny admission."""
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_PAYLOAD:
        return "DENY_INVALID_SIZE"
    if not isinstance(signature, bytes) or len(signature) != SIGNATURE_BYTES:
        return "DENY_INVALID_SIGNATURE"
    if not isinstance(trusted_public_key, bytes) or len(trusted_public_key) != PUBLIC_KEY_BYTES:
        return "DENY_TRUST_ROOT_UNAVAILABLE"
    try:
        key = Ed25519PublicKey.from_public_bytes(trusted_public_key)
        key.verify(signature, payload)
    except (InvalidSignature, ValueError):
        return "DENY_INVALID_SIGNATURE"
    # Cryptographic authenticity is necessary but not sufficient for a
    # process-bound, revocable, transaction-scoped authorization.
    return "DENY_SESSION_AND_COMMIT_BINDING_UNAVAILABLE"
