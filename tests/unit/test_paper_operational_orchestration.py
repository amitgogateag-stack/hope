from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from hope.application.jobs import create_scheduled_job_run
from hope.application.market_data.calendar import MarketSessionCalendar
from hope.domain.strategy.candidates import StrategyCandidateState, StrategyMarket
from hope.infrastructure.repositories.strategy_candidates import CurrentStrategyCandidateRecord
from hope.infrastructure.scheduling.paper import (
    OperationalPaperJobBinding,
    OperationalPaperSchedule,
    build_operational_paper_registry,
    build_operational_paper_runs,\n    run_due_operational_paper_jobs,
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


def _schedule(binding: OperationalPaperJobBinding, *, offset=timedelta(minutes=5)):
    return OperationalPaperSchedule(
        strategy_version_id=binding.strategy_version_id,
        market=binding.market,
        job_key=binding.job_key,
        session_offset=offset,
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
        build_operational_paper_registry([candidate], [_binding(candidate, market=StrategyMarket.USA)])


def test_operational_paper_registry_rejects_duplicate_market_binding() -> None:
    candidate = _candidate()
    with pytest.raises(ValueError, match="PAPER_ORCHESTRATION_DUPLICATE_MARKET_BINDING"):
        build_operational_paper_registry(
            [candidate],
            [_binding(candidate, key="paper-us-open"), _binding(candidate, key="paper-us-close")],
        )


def test_operational_paper_registry_rejects_more_than_three_operational_candidates() -> None:
    candidates = [_candidate() for _ in range(4)]
    with pytest.raises(ValueError, match="PAPER_ORCHESTRATION_OPERATIONAL_CAPACITY_EXCEEDED"):
        build_operational_paper_registry(candidates, [_binding(c, key=f"paper-{i}") for i, c in enumerate(candidates)])


def test_operational_paper_schedule_materializes_only_declared_market_sessions() -> None:
    candidate = _candidate()
    binding = _binding(candidate)
    calendar = MarketSessionCalendar(
        sessions=(
            (datetime(2026, 9, 23, 13, 30, tzinfo=UTC), datetime(2026, 9, 23, 20, 0, tzinfo=UTC)),
            (datetime(2026, 9, 24, 13, 30, tzinfo=UTC), datetime(2026, 9, 24, 20, 0, tzinfo=UTC)),
        )
    )
    runs = build_operational_paper_runs(
        [binding],
        [_schedule(binding)],
        {StrategyMarket.USA: calendar},
        start=datetime(2026, 9, 23, tzinfo=UTC),
        end=datetime(2026, 9, 25, tzinfo=UTC),
    )
    assert [run.scheduled_for for run in runs] == [
        datetime(2026, 9, 23, 13, 35, tzinfo=UTC),
        datetime(2026, 9, 24, 13, 35, tzinfo=UTC),
    ]
    assert all(run.job_key == "paper-us" for run in runs)


def test_operational_paper_schedule_requires_every_approved_binding() -> None:
    candidate = _candidate()
    binding = _binding(candidate)
    with pytest.raises(ValueError, match="PAPER_SCHEDULE_APPROVED_BINDING_UNSCHEDULED"):
        build_operational_paper_runs(
            [binding],
            [],
            {StrategyMarket.USA: MarketSessionCalendar(sessions=())},
            start=datetime(2026, 9, 23, tzinfo=UTC),
            end=datetime(2026, 9, 24, tzinfo=UTC),
        )


def test_operational_paper_schedule_rejects_unapproved_job() -> None:
    candidate = _candidate()
    binding = _binding(candidate)
    rogue = OperationalPaperSchedule(
        strategy_version_id=candidate.strategy_version_id,
        market=StrategyMarket.USA,
        job_key="rogue-paper-job",
        session_offset=timedelta(),
    )
    with pytest.raises(ValueError, match="PAPER_SCHEDULE_WITHOUT_APPROVED_BINDING"):
        build_operational_paper_runs(
            [binding],
            [rogue],
            {StrategyMarket.USA: MarketSessionCalendar(sessions=())},
            start=datetime(2026, 9, 23, tzinfo=UTC),
            end=datetime(2026, 9, 24, tzinfo=UTC),
        )


def test_operational_paper_schedule_rejects_offset_outside_session() -> None:
    candidate = _candidate()
    binding = _binding(candidate)
    calendar = MarketSessionCalendar(
        sessions=((datetime(2026, 9, 23, 13, 30, tzinfo=UTC), datetime(2026, 9, 23, 20, 0, tzinfo=UTC)),)
    )
    with pytest.raises(ValueError, match="PAPER_SCHEDULE_OFFSET_OUTSIDE_SESSION"):
        build_operational_paper_runs(
            [binding],
            [_schedule(binding, offset=timedelta(hours=7))],
            {StrategyMarket.USA: calendar},
            start=datetime(2026, 9, 23, tzinfo=UTC),
            end=datetime(2026, 9, 24, tzinfo=UTC),
        )


def test_operational_paper_schedule_requires_authoritative_market_calendar() -> None:
    candidate = _candidate()
    binding = _binding(candidate)
    with pytest.raises(ValueError, match="PAPER_SCHEDULE_MARKET_CALENDAR_REQUIRED"):
        build_operational_paper_runs(
            [binding],
            [_schedule(binding)],
            {},
            start=datetime(2026, 9, 23, tzinfo=UTC),
            end=datetime(2026, 9, 24, tzinfo=UTC),
        )



def test_due_paper_runner_executes_only_due_runs_in_deterministic_order(monkeypatch) -> None:
    now = datetime(2026, 9, 23, 14, 0, tzinfo=UTC)
    due_late = create_scheduled_job_run("paper-b", now - timedelta(minutes=1))
    due_early = create_scheduled_job_run("paper-a", now - timedelta(minutes=2))
    future = create_scheduled_job_run("paper-c", now + timedelta(minutes=1))
    calls = []

    def fake_run(engine, job_run, registry, *, now):
        calls.append(job_run)
        from hope.application.paper.runner import PaperCycleOutcome
        return PaperCycleOutcome.EXECUTED

    monkeypatch.setattr("hope.infrastructure.scheduling.paper.run_paper_once", fake_run)
    registry = __import__("hope.infrastructure.paper_runtime", fromlist=["PaperJobRegistry"]).PaperJobRegistry(
        [
            __import__("hope.infrastructure.paper_runtime", fromlist=["PaperJobDefinition"]).PaperJobDefinition("paper-a", lambda runtime: None),
            __import__("hope.infrastructure.paper_runtime", fromlist=["PaperJobDefinition"]).PaperJobDefinition("paper-b", lambda runtime: None),
            __import__("hope.infrastructure.paper_runtime", fromlist=["PaperJobDefinition"]).PaperJobDefinition("paper-c", lambda runtime: None),
        ]
    )
    results = run_due_operational_paper_jobs(
        object(),
        registry,
        [future, due_late, due_early],
        now=lambda: now,
    )
    assert calls == [due_early, due_late]
    assert [run for run, _ in results] == [due_early, due_late]


def test_due_paper_runner_rejects_duplicate_durable_run_identity(monkeypatch) -> None:
    now = datetime(2026, 9, 23, 14, 0, tzinfo=UTC)
    run = create_scheduled_job_run("paper-us", now)
    registry = __import__("hope.infrastructure.paper_runtime", fromlist=["PaperJobRegistry"]).PaperJobRegistry(
        [__import__("hope.infrastructure.paper_runtime", fromlist=["PaperJobDefinition"]).PaperJobDefinition("paper-us", lambda runtime: None)]
    )
    with pytest.raises(ValueError, match="PAPER_SCHEDULER_DUPLICATE_JOB_RUN"):
        run_due_operational_paper_jobs(object(), registry, [run, run], now=lambda: now)


def test_due_paper_runner_rejects_nonregistry_before_engine_use() -> None:
    with pytest.raises(TypeError, match="PAPER_SCHEDULER_REQUIRES_JOB_REGISTRY"):
        run_due_operational_paper_jobs(
            object(),
            object(),
            [],
            now=lambda: datetime(2026, 9, 23, 14, 0, tzinfo=UTC),
        )
