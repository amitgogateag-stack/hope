"""Fail-closed trust-policy model for isolated M4 process admission.

This is a specification model, NOT a production admission implementation.
No external worker can supply the trusted generation or release attestation.
A future native supervisor must provide them over an authenticated channel.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SupervisorEvidence:
    process_generation: str
    release_digest: str
    code_digest: str
    session_nonce: str
    supervisor_authenticated: bool


@dataclass(frozen=True)
class AdmissionExpectation:
    process_generation: str
    release_digest: str
    code_digest: str
    session_nonce: str


def evaluate_trust(evidence: SupervisorEvidence | None, expected: AdmissionExpectation) -> str:
    """Return an explicit denial unless all independent evidence matches.

    This deliberately has NO ALLOW outcome. Native attestation and COMMIT
    coordination are not implemented; matching claims cannot authorize work.
    """
    if evidence is None or not evidence.supervisor_authenticated:
        return "DENY_UNAUTHENTICATED_SUPERVISOR"
    if not all((evidence.process_generation, evidence.release_digest,
                evidence.code_digest, evidence.session_nonce)):
        return "DENY_INCOMPLETE_EVIDENCE"
    if evidence.process_generation != expected.process_generation:
        return "DENY_PROCESS_GENERATION_MISMATCH"
    if evidence.release_digest != expected.release_digest:
        return "DENY_RELEASE_MISMATCH"
    if evidence.code_digest != expected.code_digest:
        return "DENY_CODE_MISMATCH"
    if evidence.session_nonce != expected.session_nonce:
        return "DENY_SESSION_MISMATCH"
    return "DENY_PRODUCTION_GATE_UNIMPLEMENTED"
