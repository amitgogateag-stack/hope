from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Iterable
from uuid import UUID

from hope.application.universe.snapshot import UniverseSnapshot
from hope.domain.market_data.context import PITMarketContext
from hope.infrastructure.repositories.execution_provenance import CertifiedExecutionPlan


@dataclass(frozen=True)
class CertifiedResearchInputs:
    """Exact immutable research inputs resolved from experiment provenance."""

    market_context: PITMarketContext
    universe_snapshot: UniverseSnapshot

    def __post_init__(self) -> None:
        if not isinstance(self.market_context, PITMarketContext):
            raise TypeError("CERTIFIED_RESEARCH_INPUTS_REQUIRE_MARKET_CONTEXT")
        if not isinstance(self.universe_snapshot, UniverseSnapshot):
            raise TypeError("CERTIFIED_RESEARCH_INPUTS_REQUIRE_UNIVERSE_SNAPSHOT")

        active_instruments = set(
            self.universe_snapshot.active_instrument_ids(self.market_context.as_of)
        )
        for bar in self.market_context.bars:
            try:
                instrument_id = UUID(bar.instrument_id)
            except ValueError as exc:
                raise ValueError("CERTIFIED_RESEARCH_INPUT_BAR_INSTRUMENT_ID_INVALID") from exc
            if instrument_id not in active_instruments:
                raise ValueError("CERTIFIED_RESEARCH_INPUT_BAR_OUTSIDE_ACTIVE_FROZEN_UNIVERSE")


ResearchExecutionHandler = Callable[[CertifiedExecutionPlan, CertifiedResearchInputs], object]


@dataclass(frozen=True)
class ResearchExecutionImplementation:
    """One explicitly registered executable bound to immutable strategy provenance."""

    strategy_version_id: UUID
    strategy_id: UUID
    strategy_version: str
    code_commit: str
    execute: ResearchExecutionHandler

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_version_id, UUID):
            raise TypeError("RESEARCH_IMPLEMENTATION_STRATEGY_VERSION_ID_REQUIRED")
        if not isinstance(self.strategy_id, UUID):
            raise TypeError("RESEARCH_IMPLEMENTATION_STRATEGY_ID_REQUIRED")
        if not self.strategy_version.strip() or self.strategy_version != self.strategy_version.strip():
            raise ValueError("RESEARCH_IMPLEMENTATION_STRATEGY_VERSION_NOT_CANONICAL")
        if not self.code_commit.strip() or self.code_commit != self.code_commit.strip():
            raise ValueError("RESEARCH_IMPLEMENTATION_CODE_COMMIT_NOT_CANONICAL")
        if not callable(self.execute):
            raise TypeError("RESEARCH_IMPLEMENTATION_EXECUTOR_MUST_BE_CALLABLE")


class ResearchExecutionRegistry:
    """Immutable allow-list keyed by the exact durable strategy-version identity."""

    def __init__(self, implementations: Iterable[ResearchExecutionImplementation]) -> None:
        registered: dict[UUID, ResearchExecutionImplementation] = {}
        for implementation in implementations:
            if not isinstance(implementation, ResearchExecutionImplementation):
                raise TypeError("RESEARCH_IMPLEMENTATION_REQUIRED")
            if implementation.strategy_version_id in registered:
                raise ValueError("RESEARCH_IMPLEMENTATION_DUPLICATE_STRATEGY_VERSION_ID")
            registered[implementation.strategy_version_id] = implementation
        self._implementations = MappingProxyType(registered)

    def resolve(self, plan: CertifiedExecutionPlan) -> ResearchExecutionImplementation:
        implementation = self._implementations.get(plan.strategy_version_id)
        if implementation is None:
            raise RuntimeError("RESEARCH_IMPLEMENTATION_NOT_REGISTERED")
        if implementation.strategy_id != plan.strategy_id:
            raise ValueError("RESEARCH_IMPLEMENTATION_STRATEGY_ID_MISMATCH")
        if implementation.strategy_version != plan.strategy_version:
            raise ValueError("RESEARCH_IMPLEMENTATION_STRATEGY_VERSION_MISMATCH")
        if implementation.code_commit != plan.code_commit:
            raise ValueError("RESEARCH_IMPLEMENTATION_CODE_COMMIT_MISMATCH")
        return implementation


class CertifiedResearchExecutor:
    """Execute research only through an implementation matching certified provenance exactly."""

    def __init__(self, registry: ResearchExecutionRegistry) -> None:
        if not isinstance(registry, ResearchExecutionRegistry):
            raise TypeError("CERTIFIED_RESEARCH_EXECUTOR_REQUIRES_REGISTRY")
        self._registry = registry

    def execute(self, plan: CertifiedExecutionPlan, inputs: CertifiedResearchInputs) -> object:
        if not isinstance(plan, CertifiedExecutionPlan):
            raise TypeError("CERTIFIED_RESEARCH_EXECUTOR_REQUIRES_EXECUTION_PLAN")
        if not isinstance(inputs, CertifiedResearchInputs):
            raise TypeError("CERTIFIED_RESEARCH_EXECUTOR_REQUIRES_CERTIFIED_INPUTS")
        implementation = self._registry.resolve(plan)
        return implementation.execute(plan, inputs)
