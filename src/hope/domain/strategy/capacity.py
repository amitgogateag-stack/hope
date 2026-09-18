from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict

from hope.domain.strategy.candidates import StrategyCandidateState


class StrategyCandidateCapacityPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    operational_target: int = 3
    backup_target: int = 2
    research_target: int = 5
    family_target: int = 10


class StrategyCandidateCapacityStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    operational_count: int
    backup_count: int
    research_count: int
    distinct_family_count: int
    operational_gap: int
    backup_gap: int
    research_gap: int
    family_gap: int


def assess_strategy_candidate_capacity(
    candidates: Iterable[object],
    *,
    policy: StrategyCandidateCapacityPolicy | None = None,
) -> StrategyCandidateCapacityStatus:
    active_policy = policy or StrategyCandidateCapacityPolicy()
    materialized = list(candidates)

    operational_count = sum(
        candidate.state is StrategyCandidateState.OPERATIONAL_CANDIDATE
        for candidate in materialized
    )
    backup_count = sum(
        candidate.state is StrategyCandidateState.BACKUP_CANDIDATE
        for candidate in materialized
    )
    research_count = sum(
        candidate.state is StrategyCandidateState.RESEARCH
        for candidate in materialized
    )
    distinct_family_count = len({candidate.family for candidate in materialized})

    return StrategyCandidateCapacityStatus(
        operational_count=operational_count,
        backup_count=backup_count,
        research_count=research_count,
        distinct_family_count=distinct_family_count,
        operational_gap=max(active_policy.operational_target - operational_count, 0),
        backup_gap=max(active_policy.backup_target - backup_count, 0),
        research_gap=max(active_policy.research_target - research_count, 0),
        family_gap=max(active_policy.family_target - distinct_family_count, 0),
    )
