"""Adversarial wire checks for the independent runner; no positive admission."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


@pytest.fixture(scope="module")
def wire_guard():
    path = Path(__file__).resolve().parents[2] / "security/run_adversarial.py"
    spec = importlib.util.spec_from_file_location("independent_wire_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify_unavailable_response


ENVELOPE = {"protocol": "hope-independent-admission-v1", "decision": "DENY",
            "reason": "TRUSTED_KERNEL_TO_DATABASE_SESSION_BINDING_UNAVAILABLE",
            "approval_issued": False}
WIRE = (json.dumps(ENVELOPE, sort_keys=True, separators=(",", ":")) + "\n").encode()


def test_only_exact_unavailable_denial_is_accepted(wire_guard):
    wire_guard(subprocess.CompletedProcess(["verifier"], 78, WIRE, b""))


@pytest.mark.parametrize(("wire", "status", "stderr"), [
    (b" " + WIRE, 78, b""), (WIRE + b" ", 78, b""),
    (WIRE.replace(b"\n", b"\r\n"), 78, b""),
    (json.dumps(ENVELOPE, indent=2).encode() + b"\n", 78, b""),
    (b'{"decision":"ALLOW",' + WIRE[1:], 78, b""),
    (WIRE.replace(b"hope-independent-admission-v1", b"wrong-protocol"), 78, b""),
    (WIRE.replace(b"TRUSTED_KERNEL_TO_DATABASE_SESSION_BINDING_UNAVAILABLE", b"INVALID_REQUEST"), 78, b""),
    (WIRE.replace(b'"approval_issued":false', b'"approval_issued":true'), 78, b""),
    (WIRE.replace(b"DENY", b"ALLOW"), 78, b""),
    (b'{"extra":false,' + WIRE[1:], 78, b""),
    (b"not json", 78, b""), (b"\xff", 78, b""),
    (WIRE, 0, b""), (WIRE, 1, b""), (WIRE, 78, b"noise"),
], ids=["prefix", "suffix", "crlf", "pretty", "duplicate", "protocol", "reason",
        "approval", "allow", "extra", "syntax", "encoding", "success-exit",
        "wrong-exit", "stderr"])
def test_noncanonical_or_false_success_cannot_count_as_containment(wire_guard, wire, status, stderr):
    with pytest.raises(ValueError, match="independent denial response mismatch"):
        wire_guard(subprocess.CompletedProcess(["verifier"], status, wire, stderr))
