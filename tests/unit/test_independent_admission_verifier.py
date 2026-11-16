"""Denial contract only; these tests do not prove independent attestation."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from hope.security.admission_verifier import MISSING_CAPABILITY, PROTOCOL, verify_request


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
])
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

