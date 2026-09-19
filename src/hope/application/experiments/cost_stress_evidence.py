from __future__ import annotations

from typing import Any, Mapping
from uuid import UUID

from hope.application.backtests.certified_evidence import is_certified_backtest_evidence
from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.cost_stress import COST_STRESS_EVIDENCE_SCHEMA
from hope.application.experiments.historical_evaluation import HISTORICAL_METRICS
from hope.infrastructure.repositories.research_stage_evidence import (
    ResearchStageEvidenceRecord,
    research_stage_evidence_fingerprint,
)


class CostStressEvidenceProducer:
    """Persist deterministic fee/cost stress evidence without mixing slippage mechanics."""

    stage = "cost_stress"

    def __init__(self, repository) -> None:
        self._repository = repository

    def produce(
        self,
        research_run_id: UUID,
        *,
        stage_protocol: Mapping[str, Any],
        scenarios: list[Mapping[str, Any]],
    ) -> ResearchStageEvidenceRecord:
        scenario_ids = stage_protocol.get("scenario_ids")
        if not isinstance(scenario_ids, list) or not scenario_ids:
            raise ValueError("COST_STRESS_SCENARIOS_PREDECLARATION_REQUIRED")
        if any(
            not isinstance(item, str)
            or item != item.strip()
            or not item
            for item in scenario_ids
        ):
            raise ValueError("COST_STRESS_SCENARIO_ID_INVALID")
        if len(set(scenario_ids)) != len(scenario_ids):
            raise ValueError("COST_STRESS_SCENARIO_ID_DUPLICATE")

        metrics = stage_protocol.get("metrics")
        if not isinstance(metrics, list) or not metrics:
            raise ValueError("COST_STRESS_METRICS_PREDECLARATION_REQUIRED")
        if any(metric not in HISTORICAL_METRICS for metric in metrics):
            raise ValueError("COST_STRESS_METRIC_INVALID")
        if len(set(metrics)) != len(metrics):
            raise ValueError("COST_STRESS_METRIC_DUPLICATE")

        if not isinstance(scenarios, list) or not scenarios:
            raise ValueError("COST_STRESS_SOURCE_SCENARIOS_REQUIRED")

        canonical_scenarios: list[dict[str, Any]] = []
        observed_ids: list[str] = []

        for source in scenarios:
            if not isinstance(source, Mapping):
                raise ValueError("COST_STRESS_SOURCE_SCENARIO_INVALID")
            scenario_id = source.get("scenario_id")
            assumptions = source.get("cost_assumptions")
            if (
                not isinstance(scenario_id, str)
                or scenario_id != scenario_id.strip()
                or not scenario_id
                or not isinstance(assumptions, dict)
                or not assumptions
            ):
                raise ValueError("COST_STRESS_SCENARIO_INVALID")
            if any("slippage" in str(key).lower() for key in assumptions):
                raise ValueError(
                    f"COST_STRESS_SLIPPAGE_ASSUMPTION_FORBIDDEN:{scenario_id}"
                )
            observed_ids.append(scenario_id)

            certified = source.get("certified_result")
            if not is_certified_backtest_evidence(certified):
                raise ValueError(f"COST_STRESS_CERTIFIED_RESULT_REQUIRED:{scenario_id}")
            backtest = certified.get("backtest")
            if not isinstance(backtest, dict):
                raise ValueError(f"COST_STRESS_CERTIFIED_RESULT_INVALID:{scenario_id}")
            source_metrics = backtest.get("metrics")
            if not isinstance(source_metrics, dict):
                raise ValueError(f"COST_STRESS_CERTIFIED_METRICS_REQUIRED:{scenario_id}")

            projected_metrics: dict[str, str] = {}
            for metric in metrics:
                value = source_metrics.get(metric)
                if not isinstance(value, str):
                    raise ValueError(f"COST_STRESS_METRIC_VALUE_INVALID:{metric}")
                projected_metrics[metric] = value

            canonical_scenarios.append(
                {
                    "scenario_id": scenario_id,
                    "cost_assumptions": dict(assumptions),
                    "metrics": projected_metrics,
                }
            )

        if observed_ids != scenario_ids:
            raise ValueError("COST_STRESS_SOURCE_SCENARIOS_MISMATCH")

        artifact = {
            "schema": COST_STRESS_EVIDENCE_SCHEMA,
            "scenarios": canonical_scenarios,
            "result_fingerprint": configuration_hash(canonical_scenarios),
        }
        record = ResearchStageEvidenceRecord(
            research_run_id=research_run_id,
            stage=self.stage,
            result_fingerprint=research_stage_evidence_fingerprint(artifact),
            canonical_result=artifact,
        )
        self._repository.persist(record)
        return record
