"""Denial contract only; these tests do not prove independent attestation."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from hope.security.admission_verifier import MAX_REQUEST_BYTES, MAX_REQUEST_DEPTH, MISSING_CAPABILITY, PROTOCOL, verify_request


@pytest.mark.parametrize("claims", [None, {}, {"verified": True},
    {"permit": "borrowed", "session_pid": os.getpid()},
    {"kernel_identity": "synthetic", "signature_valid": True}])
def test_worker_claims_cannot_enable_verifier(claims):
    result = verify_request(claims)
    assert result == {"protocol": PROTOCOL, "decision": "DENY",
                      "reason": MISSING_CAPABILITY, "approval_issued": False}


@pytest.mark.parametrize(("payload", "reason"), [
    (b"{}", MISSING_CAPABILITY), (b"not json", "INVALID_REQUEST"),
    (b"[]", "INVALID_REQUEST"), (b"x" * 16385, "INVALID_REQUEST"),
    (b'{"verified":true,"verified":false}', "INVALID_REQUEST"),
    (b'\xff', "INVALID_REQUEST"),
    ('{}'.encode('utf-16'), "INVALID_REQUEST"),
    ('{}'.encode('utf-16-le'), "INVALID_REQUEST"),
    ('{}'.encode('utf-16-be'), "INVALID_REQUEST"),
    ('{}'.encode('utf-32'), "INVALID_REQUEST"),
    ('{}'.encode('utf-32-le'), "INVALID_REQUEST"),
    ('{}'.encode('utf-32-be'), "INVALID_REQUEST"),
    (b'{"x":NaN}', "INVALID_REQUEST"),
    (b'{"x":Infinity}', "INVALID_REQUEST"),
    (b'{"x":-Infinity}', "INVALID_REQUEST"),
    (b'{"x":' + b'[' * 7000 + b'0' + b']' * 7000 + b'}', "INVALID_REQUEST"),
    (b'{"x":' + b'[' * (MAX_REQUEST_DEPTH - 1) + b'0' + b']' * (MAX_REQUEST_DEPTH - 1) + b'}', MISSING_CAPABILITY),
    (b'{"x":' + b'[' * MAX_REQUEST_DEPTH + b'0' + b']' * MAX_REQUEST_DEPTH + b'}', "INVALID_REQUEST"),
    (json.dumps({"x": "[{}]" * MAX_REQUEST_DEPTH}).encode(), MISSING_CAPABILITY),
    (b'{}' + b' ' * (MAX_REQUEST_BYTES - 2), MISSING_CAPABILITY),
    (b'{}' + b' ' * (MAX_REQUEST_BYTES - 1), "INVALID_REQUEST"),
], ids=["object","syntax","array","oversized","duplicate","invalid-byte","utf16-bom","utf16-le","utf16-be","utf32-bom","utf32-le","utf32-be","nan","infinity","negative-infinity","decoder-depth-refusal","depth-boundary","depth-overflow","string-brackets","byte-boundary","byte-overflow"])
def test_separate_executable_returns_exact_canonical_denial(payload, reason):
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")}
    result = subprocess.run([sys.executable, "-m", "hope.security.admission_verifier"],
        input=payload, capture_output=True, env=env, timeout=10, check=False)
    assert result.returncode == 78
    evidence = json.loads(result.stdout)
    assert evidence == {"protocol": PROTOCOL, "decision": "DENY",
                        "reason": reason, "approval_issued": False}
    assert result.stderr == b""
    assert result.stdout == (json.dumps(evidence, sort_keys=True, separators=(",", ":")) + "\n").encode()

