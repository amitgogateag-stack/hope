from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Mapping
from uuid import UUID

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.historical_evaluation import HISTORICAL_METRICS
from hope.application.experiments.universe_perturbation import (
    UNIVERSE_PERTURBATION_EVIDENCE_SCHEMA,
)
from hope.infrastructure.repositories.research_stage_evidence import (
    ResearchStageEvidenceRecord,
    research_stage_evidence_fingerprint,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class UniversePerturbationEvidenceProducer:
    """Persist PIT-bound universe perturbation evidence from verified durable source runs."""

    stage = "universe_perturbation"

    def __init__(self, repository, source_resolver) -> None:
        self._repository = repository
        self._sources = source_resolver

    def produce(
        self,
        research_run_id: UUID,
        *,
        stage_protocol: Mapping[str, Any],
        perturbations: list[Mapping[str, Any]],
    ) -> ResearchStageEvidenceRecord:
        perturbation_ids = stage_protocol.get("perturbation_ids")
        if not isinstance(perturbation_ids, list) or not perturbation_ids:
            raise ValueError("UNIVERSE_PERTURBATION_PREDECLARATION_REQUIRED")
        if any(
            not isinstance(item, str) or item != item.strip() or not item
            for item in perturbation_ids
        ):
            raise ValueError("UNIVERSE_PERTURBATION_ID_INVALID")
        if len(set(perturbation_ids)) != len(perturbation_ids):
            raise ValueError("UNIVERSE_PERTURBATION_ID_DUPLICATE")

        definitions = stage_protocol.get("perturbation_definitions")
        if not isinstance(definitions, dict) or set(definitions) != set(perturbation_ids):
            raise ValueError("UNIVERSE_PERTURBATION_DEFINITIONS_PREDECLARATION_REQUIRED")
        for perturbation_id in perturbation_ids:
            definition = definitions.get(perturbation_id)
            if not isinstance(definition, dict) or not definition:
                raise ValueError(
                    f"UNIVERSE_PERTURBATION_DEFINITION_INVALID:{perturbation_id}"
                )
            self._validate_definition(perturbation_id, definition)

        metrics = stage_protocol.get("metrics")
        if not isinstance(metrics, list) or not metrics:
            raise ValueError("UNIVERSE_PERTURBATION_METRICS_PREDECLARATION_REQUIRED")
        if any(metric not in HISTORICAL_METRICS for metric in metrics):
            raise ValueError("UNIVERSE_PERTURBATION_METRIC_INVALID")
        if len(set(metrics)) != len(metrics):
            raise ValueError("UNIVERSE_PERTURBATION_METRIC_DUPLICATE")

        if not isinstance(perturbations, list) or not perturbations:
            raise ValueError("UNIVERSE_PERTURBATION_SOURCE_REQUIRED")

        canonical_items: list[dict[str, Any]] = []
        observed_ids: list[str] = []

        for source in perturbations:
            if not isinstance(source, Mapping):
                raise ValueError("UNIVERSE_PERTURBATION_SOURCE_ITEM_INVALID")
            perturbation_id = source.get("perturbation_id")
            definition = source.get("universe_definition")
            if (
                not isinstance(perturbation_id, str)
                or perturbation_id != perturbation_id.strip()
                or not perturbation_id
                or not isinstance(definition, dict)
                or not definition
            ):
                raise ValueError("UNIVERSE_PERTURBATION_ITEM_INVALID")
            observed_ids.append(perturbation_id)

            self._validate_definition(perturbation_id, definition)
            if definition != definitions.get(perturbation_id):
                raise ValueError(
                    f"UNIVERSE_PERTURBATION_PREDECLARED_DEFINITION_MISMATCH:{perturbation_id}"
                )

            source_run_id = source.get("source_research_run_id")
            if not isinstance(source_run_id, UUID):
                raise ValueError(
                    f"UNIVERSE_PERTURBATION_SOURCE_RUN_ID_REQUIRED:{perturbation_id}"
                )
            certified = self._sources.resolve(source_run_id)

            research = certified.get("research_provenance")
            if not isinstance(research, dict):
                raise ValueError(
                    f"UNIVERSE_PERTURBATION_RESEARCH_PROVENANCE_REQUIRED:{perturbation_id}"
                )
            for field in ("universe_version_id", "universe_membership_hash", "as_of"):
                if research.get(field) != definition[field]:
                    raise ValueError(
                        f"UNIVERSE_PERTURBATION_PIT_BINDING_MISMATCH:{perturbation_id}:{field}"
                    )

            backtest = certified.get("backtest")
            result = backtest.get("result") if isinstance(backtest, dict) else None
            source_metrics = result.get("metrics") if isinstance(result, dict) else None
            if not isinstance(source_metrics, dict):
                raise ValueError(
                    f"UNIVERSE_PERTURBATION_CERTIFIED_METRICS_REQUIRED:{perturbation_id}"
                )

            projected_metrics: dict[str, str] = {}
            for metric in metrics:
                value = source_metrics.get(metric)
                if not isinstance(value, str):
                    raise ValueError(
                        f"UNIVERSE_PERTURBATION_METRIC_VALUE_INVALID:{metric}"
                    )
                projected_metrics[metric] = value

            canonical_items.append(
                {
                    "perturbation_id": perturbation_id,
                    "source_research_run_id": str(source_run_id),
                    "universe_definition": dict(definition),
                    "metrics": projected_metrics,
                }
            )

        if observed_ids != perturbation_ids:
            raise ValueError("UNIVERSE_PERTURBATION_SOURCE_IDS_MISMATCH")

        artifact = {
            "schema": UNIVERSE_PERTURBATION_EVIDENCE_SCHEMA,
            "perturbations": canonical_items,
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

    @staticmethod
    def _validate_definition(perturbation_id: str, definition: Mapping[str, Any]) -> None:
        method = definition.get("method")
        version_id = definition.get("universe_version_id")
        membership_hash = definition.get("universe_membership_hash")
        as_of = definition.get("as_of")
        if not isinstance(method, str) or not method.strip() or method != method.strip():
            raise ValueError(
                f"UNIVERSE_PERTURBATION_DEFINITION_INVALID:{perturbation_id}"
            )
        try:
            parsed_version = UUID(str(version_id))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError(
                f"UNIVERSE_PERTURBATION_DEFINITION_INVALID:{perturbation_id}"
            ) from exc
        if str(parsed_version) != str(version_id):
            raise ValueError(
                f"UNIVERSE_PERTURBATION_DEFINITION_INVALID:{perturbation_id}"
            )
        if not isinstance(membership_hash, str) or _SHA256_RE.fullmatch(membership_hash) is None:
            raise ValueError(
                f"UNIVERSE_PERTURBATION_DEFINITION_INVALID:{perturbation_id}"
            )
        if not isinstance(as_of, str):
            raise ValueError(
                f"UNIVERSE_PERTURBATION_DEFINITION_INVALID:{perturbation_id}"
            )
        try:
            parsed_as_of = datetime.fromisoformat(as_of)
        except ValueError as exc:
            raise ValueError(
                f"UNIVERSE_PERTURBATION_DEFINITION_INVALID:{perturbation_id}"
            ) from exc
        if (
            parsed_as_of.tzinfo is None
            or parsed_as_of.utcoffset() is None
            or parsed_as_of.isoformat() != as_of
        ):
            raise ValueError(
                f"UNIVERSE_PERTURBATION_DEFINITION_INVALID:{perturbation_id}"
            )
