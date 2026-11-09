"""Denial contract only; these tests do not prove independent attestation."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from hope.security.admission_verifier import MISSING_CAPABILITY, verify_request


@pytest.mark.parametrize("claims", [None, {}, {"verified": True},
    {"permit": "borrowed", "session_pid": os.getpid()},
    {"kernel_identity": "synthetic", "signature_valid": True}])
def test_worker_claims_cannot_enable_verifier(claims):
    result = verify_request(claims)
    assert result["decision"] == "DENY"
    assert result["approval_issued"] is False
    assert result["reason"] == MISSING_CAPABILITY


@pytest.mark.parametrize("payload", [b"{}", b"not json", b"[]", b"x" * 16385])
def test_separate_executable_never_reports_success(payload):
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")}
    result = subprocess.run([sys.executable, "-m", "hope.security.admission_verifier"],
        input=payload, capture_output=True, env=env, timeout=10, check=False)
    assert result.returncode == 78
    evidence = json.loads(result.stdout)
    assert evidence["decision"] == "DENY"
    assert evidence["approval_issued"] is False

