"""Fail-closed provenance for strategy-family evaluation evidence."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json

from hope.application.experiments.strategy_family_evaluation import BoundStrategyFamilyEvaluation


class StrategyFamilyEvidenceProvenanceError(ValueError):
    """Raised when research evidence provenance cannot be authenticated."""


@dataclass(frozen=True, slots=True)
class StrategyFamilyEvidenceEnvelope:
    research_context_hash: str
    evidence_fingerprint: str
    envelope_hash: str


def _require_sha256(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64:
        raise StrategyFamilyEvidenceProvenanceError(f"{field} must be a sha256 hex digest")
    try:
        int(value, 16)
    except ValueError as exc:
        raise StrategyFamilyEvidenceProvenanceError(f"{field} must be a sha256 hex digest") from exc
    return value.lower()


def _envelope_hash(*, research_context_hash: str, evidence_fingerprint: str) -> str:
    payload = json.dumps(
        {"evidence_fingerprint": evidence_fingerprint, "research_context_hash": research_context_hash},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _authenticated_context_hash(context: BoundStrategyFamilyEvaluation) -> str:
    if not isinstance(context, BoundStrategyFamilyEvaluation):
        raise StrategyFamilyEvidenceProvenanceError(
            "context must be an authenticated BoundStrategyFamilyEvaluation"
        )
    if context.research_only is not True or context.family_binding.research_only is not True:
        raise StrategyFamilyEvidenceProvenanceError("context must remain RESEARCH_ONLY")
    return _require_sha256(context.research_context_hash, field="research_context_hash")


def bind_strategy_family_evidence(
    *, context: BoundStrategyFamilyEvaluation, evidence_fingerprint: str
) -> StrategyFamilyEvidenceEnvelope:
    context_hash = _authenticated_context_hash(context)
    fingerprint = _require_sha256(evidence_fingerprint, field="evidence_fingerprint")
    return StrategyFamilyEvidenceEnvelope(
        research_context_hash=context_hash,
        evidence_fingerprint=fingerprint,
        envelope_hash=_envelope_hash(
            research_context_hash=context_hash, evidence_fingerprint=fingerprint
        ),
    )


def authenticate_strategy_family_evidence(
    *,
    context: BoundStrategyFamilyEvaluation,
    envelope: StrategyFamilyEvidenceEnvelope,
    evidence_fingerprint: str,
) -> None:
    expected_context_hash = _authenticated_context_hash(context)
    if not isinstance(envelope, StrategyFamilyEvidenceEnvelope):
        raise StrategyFamilyEvidenceProvenanceError(
            "envelope must be a StrategyFamilyEvidenceEnvelope"
        )
    envelope_context_hash = _require_sha256(
        envelope.research_context_hash, field="envelope.research_context_hash"
    )
    expected_fingerprint = _require_sha256(evidence_fingerprint, field="evidence_fingerprint")
    envelope_fingerprint = _require_sha256(
        envelope.evidence_fingerprint, field="envelope.evidence_fingerprint"
    )
    envelope_hash = _require_sha256(envelope.envelope_hash, field="envelope.envelope_hash")
    if envelope_context_hash != expected_context_hash:
        raise StrategyFamilyEvidenceProvenanceError("research evidence context mismatch")
    if envelope_fingerprint != expected_fingerprint:
        raise StrategyFamilyEvidenceProvenanceError("research evidence fingerprint mismatch")
    if envelope_hash != _envelope_hash(
        research_context_hash=expected_context_hash,
        evidence_fingerprint=expected_fingerprint,
    ):
        raise StrategyFamilyEvidenceProvenanceError("research evidence provenance envelope is not authentic")
