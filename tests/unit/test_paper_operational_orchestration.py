from datetime import UTC, datetime
from uuid import uuid4

import pytest

from hope.application.jobs import create_scheduled_job_run
from hope.domain.strategy.candidates import StrategyCandidateState, StrategyMarket
from hope.infrastructure.repositories.strategy_candidates import CurrentStrategyCandidateRecord
from hope.infrastructure.scheduling.paper import (
    OperationalPaperJobBinding,
    build_operational_paper_registry,
)


def _candidate(
    *,
    state: StrategyCandidateState = StrategyCandidateState.OPERATIONAL_CANDIDATE,
    markets: tuple[StrategyMarket, ...] = (StrategyMarket.USA,),
) -> CurrentStrategyCandidateRecord:
    return CurrentStrategyCandidateRecord(
        classification_id=uuid4(),
        classification_sequence=1,
        strategy_version_id=uuid4(),
        family="TEST_FAMILY",
        markets=markets,
        state=state,
        research_decision_id=("decision-1" if state is not StrategyCandidateState.RESEARCH else None),
        rationale="Current evidence-backed classification",
        created_at=datetime(2026, 9, 23, tzinfo=UTC),
    )


def _binding(candidate: CurrentStrategyCandidateRecord, *, market=StrategyMarket.USA, key="paper-us"):
    return OperationalPaperJobBinding(
        strategy_version_id=candidate.strategy_version_id,
        market=market,
        job_key=key,
        work=lambda runtime: None,
    )


def test_operational_paper_registry_resolves_only_explicit_operational_binding() -> None:
    candidate = _candidate()
    registry = build_operational_paper_registry([candidate], [_binding(candidate)])

    run = create_scheduled_job_run("paper-us", datetime(2026, 9, 23, 14, 0, tzinfo=UTC))
    assert callable(registry.resolve(run))


def test_operational_paper_registry_rejects_nonoperational_binding() -> None:
    candidate = _candidate(state=StrategyCandidateState.BACKUP_CANDIDATE)

    with pytest.raises(ValueError, match="PAPER_ORCHESTRATION_CANDIDATE_NOT_OPERATIONAL"):
        build_operational_paper_registry([candidate], [_binding(candidate)])


def test_operational_paper_registry_rejects_unbound_operational_candidate() -> None:
    candidate = _candidate()

    with pytest.raises(ValueError, match="PAPER_ORCHESTRATION_OPERATIONAL_CANDIDATE_UNBOUND"):
        build_operational_paper_registry([candidate], [])


def test_operational_paper_registry_rejects_binding_outside_candidate_market_scope() -> None:
    candidate = _candidate(markets=(StrategyMarket.INDIA,))

    with pytest.raises(ValueError, match="PAPER_ORCHESTRATION_MARKET_NOT_ELIGIBLE"):
        build_operational_paper_registry(
            [candidate],
            [_binding(candidate, market=StrategyMarket.USA)],
        )


def test_operational_paper_registry_rejects_duplicate_market_binding() -> None:
    candidate = _candidate()

    with pytest.raises(ValueError, match="PAPER_ORCHESTRATION_DUPLICATE_MARKET_BINDING"):
        build_operational_paper_registry(
            [candidate],
            [
                _binding(candidate, key="paper-us-open"),
                _binding(candidate, key="paper-us-close"),
            ],
        )


def test_operational_paper_registry_rejects_more_than_three_operational_candidates() -> None:
    candidates = [_candidate() for _ in range(4)]

    with pytest.raises(ValueError, match="PAPER_ORCHESTRATION_OPERATIONAL_CAPACITY_EXCEEDED"):
        build_operational_paper_registry(candidates, [_binding(c, key=f"paper-{i}") for i, c in enumerate(candidates)])
