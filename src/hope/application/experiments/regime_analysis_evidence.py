from __future__ import annotations

from typing import Any, Mapping
from uuid import UUID

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.historical_evaluation import HISTORICAL_METRICS
from hope.application.experiments.regime_analysis import REGIME_ANALYSIS_EVIDENCE_SCHEMA
from hope.infrastructure.repositories.research_stage_evidence import (
    ResearchStageEvidenceRecord,
    research_stage_evidence_fingerprint,
)


class RegimeAnalysisEvidenceProducer:
    """Persist preclassified regime evidence from verified durable source runs."""

    stage = "regime_analysis"

    def __init__(self, repository, source_resolver) -> None:
        self._repository = repository
        self._sources = source_resolver

    def produce(
        self,
        research_run_id: UUID,
        *,
        stage_protocol: Mapping[str, Any],
        regimes: list[Mapping[str, Any]],
    ) -> ResearchStageEvidenceRecord:
        regime_ids = stage_protocol.get("regime_ids")
        if not isinstance(regime_ids, list) or not regime_ids:
            raise ValueError("REGIME_ANALYSIS_REGIMES_PREDECLARATION_REQUIRED")
        if any(
            not isinstance(regime_id, str)
            or not regime_id.strip()
            or regime_id != regime_id.strip()
            for regime_id in regime_ids
        ):
            raise ValueError("REGIME_ANALYSIS_REGIME_ID_INVALID")
        if len(set(regime_ids)) != len(regime_ids):
            raise ValueError("REGIME_ANALYSIS_REGIME_ID_DUPLICATE")

        metrics = stage_protocol.get("metrics")
        if not isinstance(metrics, list) or not metrics:
            raise ValueError("REGIME_ANALYSIS_METRICS_PREDECLARATION_REQUIRED")
        if any(metric not in HISTORICAL_METRICS for metric in metrics):
            raise ValueError("REGIME_ANALYSIS_METRIC_INVALID")
        if len(set(metrics)) != len(metrics):
            raise ValueError("REGIME_ANALYSIS_METRIC_DUPLICATE")

        if not isinstance(regimes, list) or not regimes:
            raise ValueError("REGIME_ANALYSIS_SOURCE_REGIMES_REQUIRED")

        canonical_regimes: list[dict[str, Any]] = []
        observed_ids: list[str] = []
        for source in regimes:
            if not isinstance(source, Mapping):
                raise ValueError("REGIME_ANALYSIS_SOURCE_REGIME_INVALID")
            regime_id = source.get("regime_id")
            if (
                not isinstance(regime_id, str)
                or not regime_id.strip()
                or regime_id != regime_id.strip()
            ):
                raise ValueError("REGIME_ANALYSIS_REGIME_ID_INVALID")
            observed_ids.append(regime_id)

            source_run_id = source.get("source_research_run_id")
            if not isinstance(source_run_id, UUID):
                raise ValueError(f"REGIME_ANALYSIS_SOURCE_RUN_ID_REQUIRED:{regime_id}")
            certified = self._sources.resolve(source_run_id)

            backtest = certified.get("backtest")
            result = backtest.get("result") if isinstance(backtest, dict) else None
            source_metrics = result.get("metrics") if isinstance(result, dict) else None
            if not isinstance(source_metrics, dict):
                raise ValueError(f"REGIME_ANALYSIS_CERTIFIED_METRICS_REQUIRED:{regime_id}")

            projected_metrics: dict[str, str] = {}
            for metric in metrics:
                value = source_metrics.get(metric)
                if not isinstance(value, str):
                    raise ValueError(f"REGIME_ANALYSIS_METRIC_VALUE_INVALID:{metric}")
                projected_metrics[metric] = value

            canonical_regimes.append(
                {
                    "regime_id": regime_id,
                    "source_research_run_id": str(source_run_id),
                    "metrics": projected_metrics,
                }
            )

        if observed_ids != regime_ids:
            raise ValueError("REGIME_ANALYSIS_SOURCE_REGIMES_MISMATCH")

        artifact = {
            "schema": REGIME_ANALYSIS_EVIDENCE_SCHEMA,
            "regimes": canonical_regimes,
            "result_fingerprint": configuration_hash(canonical_regimes),
        }
        record = ResearchStageEvidenceRecord(
            research_run_id=research_run_id,
            stage=self.stage,
            result_fingerprint=research_stage_evidence_fingerprint(artifact),
            canonical_result=artifact,
        )
        self._repository.persist(record)
        return record
