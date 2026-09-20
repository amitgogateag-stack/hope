from __future__ import annotations

from typing import Any, Mapping
from uuid import UUID

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.historical_evaluation import HISTORICAL_METRICS
from hope.application.experiments.slippage_stress import SLIPPAGE_STRESS_EVIDENCE_SCHEMA
from hope.infrastructure.repositories.research_stage_evidence import (
    ResearchStageEvidenceRecord,
    research_stage_evidence_fingerprint,
)


class SlippageStressEvidenceProducer:
    """Persist deterministic slippage stress evidence from verified durable source runs."""

    stage = "slippage_stress"

    def __init__(self, repository, source_resolver) -> None:
        self._repository = repository
        self._sources = source_resolver

    def produce(
        self,
        research_run_id: UUID,
        *,
        stage_protocol: Mapping[str, Any],
        scenarios: list[Mapping[str, Any]],
    ) -> ResearchStageEvidenceRecord:
        scenario_ids = stage_protocol.get("scenario_ids")
        if not isinstance(scenario_ids, list) or not scenario_ids:
            raise ValueError("SLIPPAGE_STRESS_SCENARIOS_PREDECLARATION_REQUIRED")
        if any(
            not isinstance(item, str)
            or item != item.strip()
            or not item
            for item in scenario_ids
        ):
            raise ValueError("SLIPPAGE_STRESS_SCENARIO_ID_INVALID")
        if len(set(scenario_ids)) != len(scenario_ids):
            raise ValueError("SLIPPAGE_STRESS_SCENARIO_ID_DUPLICATE")

        definitions = stage_protocol.get("scenario_definitions")
        if not isinstance(definitions, dict) or set(definitions) != set(scenario_ids):
            raise ValueError("SLIPPAGE_STRESS_DEFINITIONS_PREDECLARATION_REQUIRED")
        for scenario_id in scenario_ids:
            definition = definitions.get(scenario_id)
            if not isinstance(definition, dict) or not definition:
                raise ValueError(
                    f"SLIPPAGE_STRESS_SCENARIO_DEFINITION_INVALID:{scenario_id}"
                )

        metrics = stage_protocol.get("metrics")
        if not isinstance(metrics, list) or not metrics:
            raise ValueError("SLIPPAGE_STRESS_METRICS_PREDECLARATION_REQUIRED")
        if any(metric not in HISTORICAL_METRICS for metric in metrics):
            raise ValueError("SLIPPAGE_STRESS_METRIC_INVALID")
        if len(set(metrics)) != len(metrics):
            raise ValueError("SLIPPAGE_STRESS_METRIC_DUPLICATE")

        if not isinstance(scenarios, list) or not scenarios:
            raise ValueError("SLIPPAGE_STRESS_SOURCE_SCENARIOS_REQUIRED")

        canonical_scenarios: list[dict[str, Any]] = []
        observed_ids: list[str] = []

        for source in scenarios:
            if not isinstance(source, Mapping):
                raise ValueError("SLIPPAGE_STRESS_SOURCE_SCENARIO_INVALID")
            scenario_id = source.get("scenario_id")
            assumptions = source.get("slippage_assumptions")
            if (
                not isinstance(scenario_id, str)
                or scenario_id != scenario_id.strip()
                or not scenario_id
                or not isinstance(assumptions, dict)
                or not assumptions
            ):
                raise ValueError("SLIPPAGE_STRESS_SCENARIO_INVALID")

            forbidden_tokens = ("commission", "fee", "tax", "brokerage")
            if any(
                any(token in str(key).lower() for token in forbidden_tokens)
                for key in assumptions
            ):
                raise ValueError(
                    f"SLIPPAGE_STRESS_COST_ASSUMPTION_FORBIDDEN:{scenario_id}"
                )
            observed_ids.append(scenario_id)
            if assumptions != definitions.get(scenario_id):
                raise ValueError(
                    f"SLIPPAGE_STRESS_PREDECLARED_DEFINITION_MISMATCH:{scenario_id}"
                )

            source_run_id = source.get("source_research_run_id")
            if not isinstance(source_run_id, UUID):
                raise ValueError(
                    f"SLIPPAGE_STRESS_SOURCE_RUN_ID_REQUIRED:{scenario_id}"
                )
            certified = self._sources.resolve(source_run_id)

            backtest = certified.get("backtest")
            result = backtest.get("result") if isinstance(backtest, dict) else None
            source_metrics = result.get("metrics") if isinstance(result, dict) else None
            if not isinstance(source_metrics, dict):
                raise ValueError(
                    f"SLIPPAGE_STRESS_CERTIFIED_METRICS_REQUIRED:{scenario_id}"
                )

            projected_metrics: dict[str, str] = {}
            for metric in metrics:
                value = source_metrics.get(metric)
                if not isinstance(value, str):
                    raise ValueError(
                        f"SLIPPAGE_STRESS_METRIC_VALUE_INVALID:{metric}"
                    )
                projected_metrics[metric] = value

            canonical_scenarios.append(
                {
                    "scenario_id": scenario_id,
                    "source_research_run_id": str(source_run_id),
                    "slippage_assumptions": dict(assumptions),
                    "metrics": projected_metrics,
                }
            )

        if observed_ids != scenario_ids:
            raise ValueError("SLIPPAGE_STRESS_SOURCE_SCENARIOS_MISMATCH")

        artifact = {
            "schema": SLIPPAGE_STRESS_EVIDENCE_SCHEMA,
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
