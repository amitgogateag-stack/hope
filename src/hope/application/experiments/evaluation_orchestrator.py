from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable
from uuid import UUID

from hope.application.experiments.evaluation_protocol import (
    REQUIRED_EVALUATION_STAGES,
    research_evaluation_plan_hash,
)
from hope.application.experiments.evaluation_results import (
    ResearchEvaluationResultDefinition,
    research_evaluation_result_fingerprint,
)


@runtime_checkable
class ResearchStageEvaluator(Protocol):
    """One deterministic implementation of one predeclared evaluation stage."""

    @property
    def stage(self) -> str:
        ...

    def evaluate(
        self,
        *,
        stage_protocol: Mapping[str, Any],
        control_evidence: Any,
        variant_evidence: Any,
    ) -> dict[str, Any]:
        ...


class ResearchEvaluationOrchestrator:
    """Execute one predeclared research stage against exact durable run evidence."""

    def __init__(
        self,
        *,
        plan_repository,
        evidence_repository,
        result_repository,
        evaluators: tuple[ResearchStageEvaluator, ...],
        stage_evidence_repositories: Mapping[str, Any] | None = None,
    ) -> None:
        self._plans = plan_repository
        self._evidence = evidence_repository
        self._results = result_repository
        self._stage_evidence = dict(stage_evidence_repositories or {})
        unknown_sources = set(self._stage_evidence) - set(REQUIRED_EVALUATION_STAGES)
        if unknown_sources:
            raise ValueError("RESEARCH_STAGE_EVIDENCE_SOURCE_STAGE_INVALID")

        registry: dict[str, ResearchStageEvaluator] = {}
        for evaluator in evaluators:
            if not isinstance(evaluator, ResearchStageEvaluator):
                raise TypeError("RESEARCH_STAGE_EVALUATOR_REQUIRED")
            if evaluator.stage not in REQUIRED_EVALUATION_STAGES:
                raise ValueError("RESEARCH_STAGE_EVALUATOR_STAGE_INVALID")
            if evaluator.stage in registry:
                raise ValueError("RESEARCH_STAGE_EVALUATOR_DUPLICATE")
            registry[evaluator.stage] = evaluator
        self._evaluators = registry

    def evaluate_stage(
        self,
        *,
        variant_experiment_id: str,
        control_run_id: UUID,
        variant_run_id: UUID,
        stage: str,
    ) -> ResearchEvaluationResultDefinition:
        if stage not in REQUIRED_EVALUATION_STAGES:
            raise ValueError("RESEARCH_EVALUATION_STAGE_INVALID")

        plan = self._plans.get(variant_experiment_id)
        if plan is None:
            raise ValueError("RESEARCH_EVALUATION_PLAN_MISSING")
        expected_protocol_hash = research_evaluation_plan_hash(plan.canonical_protocol)
        if plan.protocol_hash != expected_protocol_hash:
            raise ValueError("RESEARCH_EVALUATION_PLAN_HASH_MISMATCH")

        stage_protocol = plan.canonical_protocol.get(stage)
        if not isinstance(stage_protocol, dict) or not stage_protocol:
            raise ValueError("RESEARCH_EVALUATION_STAGE_PROTOCOL_MISSING")

        evaluator = self._evaluators.get(stage)
        if evaluator is None:
            raise ValueError("RESEARCH_STAGE_EVALUATOR_MISSING")

        if stage == "historical_evaluation":
            source = self._evidence
        else:
            source = self._stage_evidence.get(stage)
            if source is None:
                raise ValueError("RESEARCH_STAGE_EVIDENCE_SOURCE_MISSING")
        control = source.get(control_run_id)
        if control is None:
            raise ValueError("RESEARCH_EVALUATION_CONTROL_EVIDENCE_MISSING")
        variant = source.get(variant_run_id)
        if variant is None:
            raise ValueError("RESEARCH_EVALUATION_VARIANT_EVIDENCE_MISSING")

        control_payload = self._stage_payload(control)
        variant_payload = self._stage_payload(variant)
        canonical_result = evaluator.evaluate(
            stage_protocol=stage_protocol,
            control_evidence=control_payload,
            variant_evidence=variant_payload,
        )
        if not isinstance(canonical_result, dict) or not canonical_result:
            raise ValueError("RESEARCH_STAGE_EVALUATOR_RESULT_INVALID")

        definition = ResearchEvaluationResultDefinition(
            variant_experiment_id=variant_experiment_id,
            control_run_id=control_run_id,
            variant_run_id=variant_run_id,
            stage=stage,
            protocol_hash=plan.protocol_hash,
            canonical_result=canonical_result,
            result_fingerprint=research_evaluation_result_fingerprint(canonical_result),
        )
        self._results.persist(definition)
        return definition

    @staticmethod
    def _stage_payload(record: Any) -> Any:
        if hasattr(record, "canonical_result"):
            return record.canonical_result
        if hasattr(record, "model_dump"):
            return record.model_dump(exclude={"created_at"})
        raise TypeError("RESEARCH_STAGE_EVIDENCE_RECORD_UNSUPPORTED")
