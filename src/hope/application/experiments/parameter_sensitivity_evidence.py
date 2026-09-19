from __future__ import annotations

from typing import Any, Mapping
from uuid import UUID

from hope.application.backtests.certified_evidence import is_certified_backtest_evidence
from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.historical_evaluation import HISTORICAL_METRICS
from hope.application.experiments.parameter_sensitivity import (
    PARAMETER_SENSITIVITY_EVIDENCE_SCHEMA,
)
from hope.infrastructure.repositories.research_stage_evidence import (
    ResearchStageEvidenceRecord,
    research_stage_evidence_fingerprint,
)


class ParameterSensitivityEvidenceProducer:
    """Persist deterministic evidence for predeclared parameter perturbation sets."""

    stage = "parameter_sensitivity"

    def __init__(self, repository) -> None:
        self._repository = repository

    def produce(
        self,
        research_run_id: UUID,
        *,
        stage_protocol: Mapping[str, Any],
        parameter_sets: list[Mapping[str, Any]],
    ) -> ResearchStageEvidenceRecord:
        parameter_set_ids = stage_protocol.get("parameter_set_ids")
        if not isinstance(parameter_set_ids, list) or not parameter_set_ids:
            raise ValueError(
                "PARAMETER_SENSITIVITY_PARAMETER_SETS_PREDECLARATION_REQUIRED"
            )
        if any(
            not isinstance(item, str)
            or item != item.strip()
            or not item
            for item in parameter_set_ids
        ):
            raise ValueError("PARAMETER_SENSITIVITY_PARAMETER_SET_ID_INVALID")
        if len(set(parameter_set_ids)) != len(parameter_set_ids):
            raise ValueError("PARAMETER_SENSITIVITY_PARAMETER_SET_ID_DUPLICATE")

        metrics = stage_protocol.get("metrics")
        if not isinstance(metrics, list) or not metrics:
            raise ValueError("PARAMETER_SENSITIVITY_METRICS_PREDECLARATION_REQUIRED")
        if any(metric not in HISTORICAL_METRICS for metric in metrics):
            raise ValueError("PARAMETER_SENSITIVITY_METRIC_INVALID")
        if len(set(metrics)) != len(metrics):
            raise ValueError("PARAMETER_SENSITIVITY_METRIC_DUPLICATE")

        if not isinstance(parameter_sets, list) or not parameter_sets:
            raise ValueError("PARAMETER_SENSITIVITY_SOURCE_PARAMETER_SETS_REQUIRED")

        canonical_sets: list[dict[str, Any]] = []
        observed_ids: list[str] = []
        for source in parameter_sets:
            if not isinstance(source, Mapping):
                raise ValueError("PARAMETER_SENSITIVITY_SOURCE_PARAMETER_SET_INVALID")
            parameter_set_id = source.get("parameter_set_id")
            parameters = source.get("parameters")
            if (
                not isinstance(parameter_set_id, str)
                or parameter_set_id != parameter_set_id.strip()
                or not parameter_set_id
                or not isinstance(parameters, dict)
                or not parameters
            ):
                raise ValueError("PARAMETER_SENSITIVITY_PARAMETER_SET_INVALID")
            observed_ids.append(parameter_set_id)

            certified = source.get("certified_result")
            if not is_certified_backtest_evidence(certified):
                raise ValueError(
                    f"PARAMETER_SENSITIVITY_CERTIFIED_RESULT_REQUIRED:{parameter_set_id}"
                )
            backtest = certified.get("backtest")
            if not isinstance(backtest, dict):
                raise ValueError(
                    f"PARAMETER_SENSITIVITY_CERTIFIED_RESULT_INVALID:{parameter_set_id}"
                )
            source_metrics = backtest.get("metrics")
            if not isinstance(source_metrics, dict):
                raise ValueError(
                    f"PARAMETER_SENSITIVITY_CERTIFIED_METRICS_REQUIRED:{parameter_set_id}"
                )

            projected_metrics: dict[str, str] = {}
            for metric in metrics:
                value = source_metrics.get(metric)
                if not isinstance(value, str):
                    raise ValueError(
                        f"PARAMETER_SENSITIVITY_METRIC_VALUE_INVALID:{metric}"
                    )
                projected_metrics[metric] = value

            canonical_sets.append(
                {
                    "parameter_set_id": parameter_set_id,
                    "parameters": dict(parameters),
                    "metrics": projected_metrics,
                }
            )

        if observed_ids != parameter_set_ids:
            raise ValueError("PARAMETER_SENSITIVITY_SOURCE_PARAMETER_SETS_MISMATCH")

        artifact = {
            "schema": PARAMETER_SENSITIVITY_EVIDENCE_SCHEMA,
            "parameter_sets": canonical_sets,
            "result_fingerprint": configuration_hash(canonical_sets),
        }
        record = ResearchStageEvidenceRecord(
            research_run_id=research_run_id,
            stage=self.stage,
            result_fingerprint=research_stage_evidence_fingerprint(artifact),
            canonical_result=artifact,
        )
        self._repository.persist(record)
        return record
