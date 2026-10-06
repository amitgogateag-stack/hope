from dataclasses import replace
from hashlib import sha256

import pytest

from hope.application.experiments.evaluation_protocol import (
    REQUIRED_EVALUATION_STAGES,
    ResearchEvaluationPlanDefinition,
)
from hope.application.experiments.strategy_family_evaluation import bind_strategy_family_evaluation
from hope.application.experiments.strategy_family_evidence_provenance import (
    StrategyFamilyEvidenceEnvelope,
    StrategyFamilyEvidenceProvenanceError,
    authenticate_strategy_family_evidence,
    bind_strategy_family_evidence,
)
from hope.domain.strategy_family import ResearchMarket, ResearchRegime


def _plan(experiment_id: str = "exp-provenance-001", *, cost_marker: str = "base") -> ResearchEvaluationPlanDefinition:
    protocol = {stage: {"enabled": True} for stage in REQUIRED_EVALUATION_STAGES}
    protocol["cost_stress"] = {"enabled": True, "marker": cost_marker}
    return ResearchEvaluationPlanDefinition(variant_experiment_id=experiment_id, protocol=protocol)


def _context(*, family="CROSS_SECTIONAL_MOMENTUM", market=ResearchMarket.USA,
             regime=ResearchRegime.BULL_TREND, experiment_id="exp-provenance-001",
             cost_marker="base"):
    return bind_strategy_family_evaluation(
        family=family,
        market=market,
        regime=regime,
        evaluation_plan=_plan(experiment_id, cost_marker=cost_marker),
    )


def _fingerprint(seed: str = "evidence") -> str:
    return sha256(seed.encode("utf-8")).hexdigest()


def test_evidence_envelope_authenticates_exact_context_and_artifact():
    context = _context()
    fingerprint = _fingerprint()
    envelope = bind_strategy_family_evidence(context=context, evidence_fingerprint=fingerprint)
    authenticate_strategy_family_evidence(context=context, envelope=envelope, evidence_fingerprint=fingerprint)
    assert envelope.research_context_hash == context.research_context_hash


def test_same_context_and_artifact_are_deterministic():
    context = _context()
    fingerprint = _fingerprint()
    assert bind_strategy_family_evidence(context=context, evidence_fingerprint=fingerprint) == bind_strategy_family_evidence(context=context, evidence_fingerprint=fingerprint)


@pytest.mark.parametrize("other_context", [
    _context(family="LOW_VOLATILITY_DEFENSIVE", regime=ResearchRegime.SIDEWAYS),
    _context(market=ResearchMarket.INDIA),
    _context(experiment_id="exp-provenance-002"),
    _context(cost_marker="stressed"),
])
def test_evidence_cannot_be_reused_under_another_research_context(other_context):
    original = _context()
    fingerprint = _fingerprint()
    envelope = bind_strategy_family_evidence(context=original, evidence_fingerprint=fingerprint)
    with pytest.raises(StrategyFamilyEvidenceProvenanceError, match="context"):
        authenticate_strategy_family_evidence(context=other_context, envelope=envelope, evidence_fingerprint=fingerprint)


def test_evidence_cannot_be_reused_for_another_artifact():
    context = _context()
    envelope = bind_strategy_family_evidence(context=context, evidence_fingerprint=_fingerprint("original"))
    with pytest.raises(StrategyFamilyEvidenceProvenanceError, match="fingerprint"):
        authenticate_strategy_family_evidence(context=context, envelope=envelope, evidence_fingerprint=_fingerprint("different"))


def test_tampered_envelope_hash_fails_closed():
    context = _context()
    fingerprint = _fingerprint()
    envelope = bind_strategy_family_evidence(context=context, evidence_fingerprint=fingerprint)
    tampered = replace(envelope, envelope_hash="0" * 64)
    with pytest.raises(StrategyFamilyEvidenceProvenanceError, match="not authentic"):
        authenticate_strategy_family_evidence(context=context, envelope=tampered, evidence_fingerprint=fingerprint)


@pytest.mark.parametrize("bad", ["", "abc", "g" * 64, 123, None])
def test_malformed_fingerprints_fail_closed(bad):
    with pytest.raises(StrategyFamilyEvidenceProvenanceError, match="sha256"):
        bind_strategy_family_evidence(context=_context(), evidence_fingerprint=bad)


def test_raw_mapping_cannot_impersonate_authenticated_context():
    with pytest.raises(StrategyFamilyEvidenceProvenanceError, match="authenticated"):
        bind_strategy_family_evidence(context={"research_context_hash": "0" * 64}, evidence_fingerprint=_fingerprint())


def test_raw_mapping_cannot_impersonate_provenance_envelope():
    context = _context()
    with pytest.raises(StrategyFamilyEvidenceProvenanceError, match="EvidenceEnvelope"):
        authenticate_strategy_family_evidence(
            context=context,
            envelope={"research_context_hash": context.research_context_hash, "evidence_fingerprint": _fingerprint(), "envelope_hash": "0" * 64},
            evidence_fingerprint=_fingerprint(),
        )


def test_raw_evaluation_like_object_cannot_impersonate_authenticated_context():
    class Forged:
        research_context_hash = "0" * 64
        research_only = True
    with pytest.raises(StrategyFamilyEvidenceProvenanceError, match="authenticated"):
        bind_strategy_family_evidence(context=Forged(), evidence_fingerprint=_fingerprint())
