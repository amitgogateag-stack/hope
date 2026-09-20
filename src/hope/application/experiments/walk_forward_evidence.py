from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping
from uuid import UUID

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.historical_evaluation import HISTORICAL_METRICS
from hope.application.experiments.walk_forward import WALK_FORWARD_EVIDENCE_SCHEMA
from hope.infrastructure.repositories.research_stage_evidence import (
    ResearchStageEvidenceRecord,
    research_stage_evidence_fingerprint,
)


class WalkForwardEvidenceProducer:
    """Build deterministic walk-forward evidence from verified durable source runs."""

    stage = "walk_forward"

    def __init__(self, repository, source_resolver) -> None:
        self._repository = repository
        self._sources = source_resolver

    def produce(
        self,
        research_run_id: UUID,
        *,
        stage_protocol: Mapping[str, Any],
        folds: list[Mapping[str, Any]],
    ) -> ResearchStageEvidenceRecord:
        fold_ids = stage_protocol.get("fold_ids")
        if not isinstance(fold_ids, list) or not fold_ids:
            raise ValueError("WALK_FORWARD_FOLDS_PREDECLARATION_REQUIRED")
        if any(
            not isinstance(fold_id, str)
            or not fold_id.strip()
            or fold_id != fold_id.strip()
            for fold_id in fold_ids
        ):
            raise ValueError("WALK_FORWARD_FOLD_ID_INVALID")
        if len(set(fold_ids)) != len(fold_ids):
            raise ValueError("WALK_FORWARD_FOLD_ID_DUPLICATE")

        fold_definitions = stage_protocol.get("fold_definitions")
        if not isinstance(fold_definitions, dict) or set(fold_definitions) != set(fold_ids):
            raise ValueError("WALK_FORWARD_FOLD_DEFINITIONS_PREDECLARATION_REQUIRED")
        for fold_id in fold_ids:
            definition = fold_definitions.get(fold_id)
            if not isinstance(definition, dict) or set(definition) != {
                "train_start", "train_end", "test_start", "test_end"
            }:
                raise ValueError(f"WALK_FORWARD_FOLD_DEFINITION_INVALID:{fold_id}")
            train_start = self._time(definition["train_start"])
            train_end = self._time(definition["train_end"])
            test_start = self._time(definition["test_start"])
            test_end = self._time(definition["test_end"])
            if not (train_start < train_end <= test_start < test_end):
                raise ValueError(f"WALK_FORWARD_FOLD_WINDOW_INVALID:{fold_id}")
            observed_window = {
                "train_start": train_start.isoformat(),
                "train_end": train_end.isoformat(),
                "test_start": test_start.isoformat(),
                "test_end": test_end.isoformat(),
            }
            if observed_window != fold_definitions.get(fold_id):
                raise ValueError(f"WALK_FORWARD_PREDECLARED_WINDOW_MISMATCH:{fold_id}")

        metrics = stage_protocol.get("metrics")
        if not isinstance(metrics, list) or not metrics:
            raise ValueError("WALK_FORWARD_METRICS_PREDECLARATION_REQUIRED")
        if any(metric not in HISTORICAL_METRICS for metric in metrics):
            raise ValueError("WALK_FORWARD_METRIC_INVALID")
        if len(set(metrics)) != len(metrics):
            raise ValueError("WALK_FORWARD_METRIC_DUPLICATE")

        if not isinstance(folds, list) or not folds:
            raise ValueError("WALK_FORWARD_SOURCE_FOLDS_REQUIRED")

        canonical_folds: list[dict[str, Any]] = []
        previous_test_end: datetime | None = None
        observed_ids: list[str] = []

        for source in folds:
            if not isinstance(source, Mapping):
                raise ValueError("WALK_FORWARD_SOURCE_FOLD_INVALID")
            fold_id = source.get("fold_id")
            if not isinstance(fold_id, str) or not fold_id.strip() or fold_id != fold_id.strip():
                raise ValueError("WALK_FORWARD_FOLD_ID_INVALID")
            observed_ids.append(fold_id)

            train_start = self._time(source.get("train_start"))
            train_end = self._time(source.get("train_end"))
            test_start = self._time(source.get("test_start"))
            test_end = self._time(source.get("test_end"))
            if not (train_start < train_end <= test_start < test_end):
                raise ValueError(f"WALK_FORWARD_FOLD_WINDOW_INVALID:{fold_id}")
            if previous_test_end is not None and test_start < previous_test_end:
                raise ValueError("WALK_FORWARD_TEST_WINDOWS_OVERLAP")
            previous_test_end = test_end

            source_run_id = source.get("source_research_run_id")
            if not isinstance(source_run_id, UUID):
                raise ValueError(f"WALK_FORWARD_SOURCE_RUN_ID_REQUIRED:{fold_id}")
            certified = self._sources.resolve(source_run_id)

            backtest = certified.get("backtest")
            result = backtest.get("result") if isinstance(backtest, dict) else None
            source_metrics = result.get("metrics") if isinstance(result, dict) else None
            if not isinstance(source_metrics, dict):
                raise ValueError(f"WALK_FORWARD_CERTIFIED_METRICS_REQUIRED:{fold_id}")

            projected_metrics: dict[str, str] = {}
            for metric in metrics:
                value = source_metrics.get(metric)
                if not isinstance(value, str):
                    raise ValueError(f"WALK_FORWARD_METRIC_VALUE_INVALID:{metric}")
                projected_metrics[metric] = value

            canonical_folds.append(
                {
                    "fold_id": fold_id,
                    "source_research_run_id": str(source_run_id),
                    "train_start": train_start.isoformat(),
                    "train_end": train_end.isoformat(),
                    "test_start": test_start.isoformat(),
                    "test_end": test_end.isoformat(),
                    "metrics": projected_metrics,
                }
            )

        if observed_ids != fold_ids:
            raise ValueError("WALK_FORWARD_SOURCE_FOLDS_MISMATCH")

        artifact = {
            "schema": WALK_FORWARD_EVIDENCE_SCHEMA,
            "folds": canonical_folds,
            "result_fingerprint": configuration_hash(canonical_folds),
        }
        record = ResearchStageEvidenceRecord(
            research_run_id=research_run_id,
            stage=self.stage,
            result_fingerprint=research_stage_evidence_fingerprint(artifact),
            canonical_result=artifact,
        )
        self._repository.persist(record)
        return record

    @staticmethod
    def _time(value: Any) -> datetime:
        if not isinstance(value, str):
            raise ValueError("WALK_FORWARD_TIME_INVALID")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("WALK_FORWARD_TIME_INVALID") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.isoformat() != value:
            raise ValueError("WALK_FORWARD_TIME_INVALID")
        return parsed
