from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
from uuid import UUID

from hope.domain.strategy.candidates import StrategyCandidateState, StrategyMarket
from hope.infrastructure.paper_runtime import PaperJobDefinition, PaperJobRegistry, PaperRegisteredWork
from hope.infrastructure.repositories.strategy_candidates import CurrentStrategyCandidateRecord


@dataclass(frozen=True)
class OperationalPaperJobBinding:
    """Explicitly bind one operational strategy candidate to one PAPER market job."""

    strategy_version_id: UUID
    market: StrategyMarket
    job_key: str
    work: PaperRegisteredWork

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_version_id, UUID):
            raise TypeError("PAPER_ORCHESTRATION_REQUIRES_STRATEGY_VERSION_ID")
        if not isinstance(self.market, StrategyMarket):
            raise TypeError("PAPER_ORCHESTRATION_REQUIRES_STRATEGY_MARKET")
        key = self.job_key.strip()
        if not key:
            raise ValueError("PAPER_ORCHESTRATION_JOB_KEY_REQUIRED")
        if key != self.job_key:
            raise ValueError("PAPER_ORCHESTRATION_JOB_KEY_NOT_CANONICAL")
        # PaperJobDefinition owns the authoritative callable/bindable-work contract.
        PaperJobDefinition(self.job_key, self.work)


def build_operational_paper_registry(
    candidates: Iterable[CurrentStrategyCandidateRecord],
    bindings: Iterable[OperationalPaperJobBinding],
) -> PaperJobRegistry:
    """Build the PAPER allow-list only from the current operational candidate set.

    This is deliberately a composition boundary, not an execution entrypoint.  Candidate
    classification never runs a strategy by itself: every operational candidate must have an
    explicit market-scoped binding, and every binding must resolve to a current operational
    candidate before the immutable PaperJobRegistry can be constructed.
    """

    current_by_version: dict[UUID, CurrentStrategyCandidateRecord] = {}
    operational_versions: set[UUID] = set()
    for candidate in candidates:
        if not isinstance(candidate, CurrentStrategyCandidateRecord):
            raise TypeError("PAPER_ORCHESTRATION_REQUIRES_CURRENT_CANDIDATE_RECORD")
        if candidate.strategy_version_id in current_by_version:
            raise ValueError("PAPER_ORCHESTRATION_DUPLICATE_CURRENT_CANDIDATE")
        current_by_version[candidate.strategy_version_id] = candidate
        if candidate.state is StrategyCandidateState.OPERATIONAL_CANDIDATE:
            if candidate.research_decision_id is None:
                raise ValueError("PAPER_ORCHESTRATION_OPERATIONAL_DECISION_REQUIRED")
            operational_versions.add(candidate.strategy_version_id)

    if len(operational_versions) > 3:
        raise ValueError("PAPER_ORCHESTRATION_OPERATIONAL_CAPACITY_EXCEEDED")

    definitions: list[PaperJobDefinition] = []
    bound_versions: set[UUID] = set()
    bound_scopes: set[tuple[UUID, StrategyMarket]] = set()
    for binding in bindings:
        if not isinstance(binding, OperationalPaperJobBinding):
            raise TypeError("PAPER_ORCHESTRATION_REQUIRES_JOB_BINDING")
        candidate = current_by_version.get(binding.strategy_version_id)
        if candidate is None:
            raise ValueError("PAPER_ORCHESTRATION_CANDIDATE_NOT_CURRENT")
        if candidate.state is not StrategyCandidateState.OPERATIONAL_CANDIDATE:
            raise ValueError("PAPER_ORCHESTRATION_CANDIDATE_NOT_OPERATIONAL")
        if binding.market not in candidate.markets:
            raise ValueError("PAPER_ORCHESTRATION_MARKET_NOT_ELIGIBLE")

        scope = (binding.strategy_version_id, binding.market)
        if scope in bound_scopes:
            raise ValueError("PAPER_ORCHESTRATION_DUPLICATE_MARKET_BINDING")
        bound_scopes.add(scope)
        bound_versions.add(binding.strategy_version_id)
        definitions.append(PaperJobDefinition(binding.job_key, binding.work))

    if bound_versions != operational_versions:
        raise ValueError("PAPER_ORCHESTRATION_OPERATIONAL_CANDIDATE_UNBOUND")

    return PaperJobRegistry(definitions)
