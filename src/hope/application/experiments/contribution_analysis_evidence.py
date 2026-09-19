from __future__ import annotations

from typing import Any, Mapping
from uuid import UUID

from hope.application.backtests.certified_evidence import is_certified_backtest_evidence
from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.contribution_analysis import (
    CONTRIBUTION_ANALYSIS_EVIDENCE_SCHEMA,
)
from hope.application.experiments.historical_evaluation import HISTORICAL_METRICS
from hope.infrastructure.repositories.research_stage_evidence import (
    ResearchStageEvidenceRecord,
    research_stage_evidence_fingerprint,
)


_FORBIDDEN_RETROSPECTIVE_TOKENS = {
    "rank",
    "ranking",
    "pnl",
    "profit",
    "return",
    "sharpe",
    "winner",
    "loser",
    "performance",
}


class ContributionAnalysisEvidenceProducer:
    """Persist evidence for predeclared, non-performance-conditioned contribution buckets."""

    stage = "contribution_analysis"

    def __init__(self, repository) -> None:
        self._repository = repository

    def produce(
        self,
        research_run_id: UUID,
        *,
        stage_protocol: Mapping[str, Any],
        contributions: list[Mapping[str, Any]],
    ) -> ResearchStageEvidenceRecord:
        contribution_ids = stage_protocol.get("contribution_ids")
        if not isinstance(contribution_ids, list) or not contribution_ids:
            raise ValueError("CONTRIBUTION_ANALYSIS_PREDECLARATION_REQUIRED")
        if any(
            not isinstance(item, str) or item != item.strip() or not item
            for item in contribution_ids
        ):
            raise ValueError("CONTRIBUTION_ANALYSIS_ID_INVALID")
        if len(set(contribution_ids)) != len(contribution_ids):
            raise ValueError("CONTRIBUTION_ANALYSIS_ID_DUPLICATE")

        definitions = stage_protocol.get("contribution_definitions")
        if not isinstance(definitions, dict) or set(definitions) != set(contribution_ids):
            raise ValueError("CONTRIBUTION_ANALYSIS_DEFINITIONS_PREDECLARATION_REQUIRED")
        for contribution_id in contribution_ids:
            definition = definitions.get(contribution_id)
            if not isinstance(definition, dict) or not definition:
                raise ValueError(
                    f"CONTRIBUTION_ANALYSIS_DEFINITION_INVALID:{contribution_id}"
                )
            self._reject_retrospective_definition(contribution_id, definition)

        metrics = stage_protocol.get("metrics")
        if not isinstance(metrics, list) or not metrics:
            raise ValueError("CONTRIBUTION_ANALYSIS_METRICS_PREDECLARATION_REQUIRED")
        if any(metric not in HISTORICAL_METRICS for metric in metrics):
            raise ValueError("CONTRIBUTION_ANALYSIS_METRIC_INVALID")
        if len(set(metrics)) != len(metrics):
            raise ValueError("CONTRIBUTION_ANALYSIS_METRIC_DUPLICATE")

        if not isinstance(contributions, list) or not contributions:
            raise ValueError("CONTRIBUTION_ANALYSIS_SOURCE_REQUIRED")

        canonical_items: list[dict[str, Any]] = []
        observed_ids: list[str] = []
        for source in contributions:
            if not isinstance(source, Mapping):
                raise ValueError("CONTRIBUTION_ANALYSIS_SOURCE_ITEM_INVALID")
            contribution_id = source.get("contribution_id")
            definition = source.get("contribution_definition")
            if (
                not isinstance(contribution_id, str)
                or contribution_id != contribution_id.strip()
                or not contribution_id
                or not isinstance(definition, dict)
                or not definition
            ):
                raise ValueError("CONTRIBUTION_ANALYSIS_ITEM_INVALID")
            observed_ids.append(contribution_id)

            expected_definition = definitions.get(contribution_id)
            if expected_definition != definition:
                raise ValueError(
                    f"CONTRIBUTION_ANALYSIS_PREDECLARED_DEFINITION_MISMATCH:{contribution_id}"
                )

            certified = source.get("certified_result")
            if not is_certified_backtest_evidence(certified):
                raise ValueError(
                    f"CONTRIBUTION_ANALYSIS_CERTIFIED_RESULT_REQUIRED:{contribution_id}"
                )
            backtest = certified.get("backtest")
            source_metrics = backtest.get("metrics") if isinstance(backtest, dict) else None
            if not isinstance(source_metrics, dict):
                raise ValueError(
                    f"CONTRIBUTION_ANALYSIS_CERTIFIED_METRICS_REQUIRED:{contribution_id}"
                )

            projected_metrics: dict[str, str] = {}
            for metric in metrics:
                value = source_metrics.get(metric)
                if not isinstance(value, str):
                    raise ValueError(
                        f"CONTRIBUTION_ANALYSIS_METRIC_VALUE_INVALID:{metric}"
                    )
                projected_metrics[metric] = value

            canonical_items.append(
                {
                    "contribution_id": contribution_id,
                    "contribution_definition": dict(definition),
                    "metrics": projected_metrics,
                }
            )

        if observed_ids != contribution_ids:
            raise ValueError("CONTRIBUTION_ANALYSIS_SOURCE_IDS_MISMATCH")

        artifact = {
            "schema": CONTRIBUTION_ANALYSIS_EVIDENCE_SCHEMA,
            "contributions": canonical_items,
            "result_fingerprint": configuration_hash(canonical_items),
        }
        record = ResearchStageEvidenceRecord(
            research_run_id=research_run_id,
            stage=self.stage,
            result_fingerprint=research_stage_evidence_fingerprint(artifact),
            canonical_result=artifact,
        )
        self._repository.persist(record)
        return record

    @classmethod
    def _reject_retrospective_definition(
        cls,
        contribution_id: str,
        definition: Mapping[str, Any],
    ) -> None:
        def visit(value: Any) -> bool:
            if isinstance(value, Mapping):
                return any(visit(key) or visit(item) for key, item in value.items())
            if isinstance(value, (list, tuple, set)):
                return any(visit(item) for item in value)
            if isinstance(value, str):
                normalized = value.lower().replace("-", "_")
                tokens = set(normalized.replace(".", "_").split("_"))
                return bool(tokens & _FORBIDDEN_RETROSPECTIVE_TOKENS)
            return False

        if visit(definition):
            raise ValueError(
                f"CONTRIBUTION_ANALYSIS_RETROSPECTIVE_DEFINITION_FORBIDDEN:{contribution_id}"
            )
