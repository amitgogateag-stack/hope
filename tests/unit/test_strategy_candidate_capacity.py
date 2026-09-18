from types import SimpleNamespace

from hope.domain.strategy.capacity import assess_strategy_candidate_capacity
from hope.domain.strategy.candidates import StrategyCandidateState


def test_candidate_capacity_reports_targets_without_inventing_candidates() -> None:
    candidates = [
        SimpleNamespace(state=StrategyCandidateState.OPERATIONAL_CANDIDATE, family="MOMENTUM"),
        SimpleNamespace(state=StrategyCandidateState.BACKUP_CANDIDATE, family="MEAN_REVERSION"),
        SimpleNamespace(state=StrategyCandidateState.RESEARCH, family="BREAKOUT"),
        SimpleNamespace(state=StrategyCandidateState.RESEARCH, family="VOLATILITY"),
    ]

    status = assess_strategy_candidate_capacity(candidates)

    assert status.operational_count == 1
    assert status.backup_count == 1
    assert status.research_count == 2
    assert status.distinct_family_count == 4
    assert status.operational_gap == 2
    assert status.backup_gap == 1
    assert status.research_gap == 3
    assert status.family_gap == 6


def test_candidate_capacity_counts_distinct_families_not_versions() -> None:
    candidates = [
        SimpleNamespace(state=StrategyCandidateState.RESEARCH, family="MOMENTUM"),
        SimpleNamespace(state=StrategyCandidateState.RESEARCH, family="MOMENTUM"),
    ]

    status = assess_strategy_candidate_capacity(candidates)

    assert status.research_count == 2
    assert status.distinct_family_count == 1
    assert status.family_gap == 9
