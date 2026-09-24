from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Mapping
from uuid import UUID

from hope.application.jobs import JobRunStatus, ScheduledJobRun, create_scheduled_job_run
from hope.application.market_data.calendar import MarketSessionCalendar
from hope.domain.strategy.candidates import StrategyCandidateState, StrategyMarket
from hope.infrastructure.paper_runtime import (
    PaperCycleOutcome,
    PaperJobDefinition,
    PaperJobRegistry,
    PaperRegisteredWork,
    run_paper_once,
)
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.strategy_candidates import CurrentStrategyCandidateRecord
from sqlalchemy import Engine


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
        PaperJobDefinition(self.job_key, self.work)

    @property
    def durable_job_key(self) -> str:
        return _durable_operational_paper_job_key(
            self.strategy_version_id,
            self.market,
            self.job_key,
        )


@dataclass(frozen=True)
class OperationalPaperSchedule:
    """Explicit market-session schedule for one already-approved PAPER job binding."""

    strategy_version_id: UUID
    market: StrategyMarket
    job_key: str
    session_offset: timedelta

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_version_id, UUID):
            raise TypeError("PAPER_SCHEDULE_REQUIRES_STRATEGY_VERSION_ID")
        if not isinstance(self.market, StrategyMarket):
            raise TypeError("PAPER_SCHEDULE_REQUIRES_STRATEGY_MARKET")
        key = self.job_key.strip()
        if not key:
            raise ValueError("PAPER_SCHEDULE_JOB_KEY_REQUIRED")
        if key != self.job_key:
            raise ValueError("PAPER_SCHEDULE_JOB_KEY_NOT_CANONICAL")
        if not isinstance(self.session_offset, timedelta):
            raise TypeError("PAPER_SCHEDULE_REQUIRES_SESSION_OFFSET")
        if self.session_offset < timedelta(0):
            raise ValueError("PAPER_SCHEDULE_OFFSET_MUST_BE_NONNEGATIVE")

    @property
    def durable_job_key(self) -> str:
        return _durable_operational_paper_job_key(
            self.strategy_version_id,
            self.market,
            self.job_key,
        )


def _durable_operational_paper_job_key(
    strategy_version_id: UUID,
    market: StrategyMarket,
    job_key: str,
) -> str:
    return f"paper:{market.value}:{strategy_version_id}:{job_key}"


def build_operational_paper_registry(
    candidates: Iterable[CurrentStrategyCandidateRecord],
    bindings: Iterable[OperationalPaperJobBinding],
) -> PaperJobRegistry:
    """Build the PAPER allow-list only from the current operational candidate set."""

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
        definitions.append(PaperJobDefinition(binding.durable_job_key, binding.work))

    if bound_versions != operational_versions:
        raise ValueError("PAPER_ORCHESTRATION_OPERATIONAL_CANDIDATE_UNBOUND")

    return PaperJobRegistry(definitions)


def build_operational_paper_runs(
    bindings: Iterable[OperationalPaperJobBinding],
    schedules: Iterable[OperationalPaperSchedule],
    calendars: Mapping[StrategyMarket, MarketSessionCalendar],
    *,
    start: datetime,
    end: datetime,
) -> tuple[ScheduledJobRun, ...]:
    """Materialize deterministic PAPER runs only from explicit bindings and market sessions.

    The exchange calendar remains authoritative and data-driven.  No weekday, holiday, timezone,
    or exchange-hours assumptions are embedded here.
    """

    window_start = _aware_schedule_time(start)
    window_end = _aware_schedule_time(end)
    if window_end <= window_start:
        raise ValueError("PAPER_SCHEDULE_WINDOW_INVALID")

    binding_keys: set[tuple[UUID, StrategyMarket, str]] = set()
    for binding in bindings:
        if not isinstance(binding, OperationalPaperJobBinding):
            raise TypeError("PAPER_ORCHESTRATION_REQUIRES_JOB_BINDING")
        key = (binding.strategy_version_id, binding.market, binding.job_key)
        if key in binding_keys:
            raise ValueError("PAPER_SCHEDULE_DUPLICATE_BINDING")
        binding_keys.add(key)

    schedules_by_key: dict[tuple[UUID, StrategyMarket, str], OperationalPaperSchedule] = {}
    for schedule in schedules:
        if not isinstance(schedule, OperationalPaperSchedule):
            raise TypeError("PAPER_SCHEDULE_DEFINITION_REQUIRED")
        key = (schedule.strategy_version_id, schedule.market, schedule.job_key)
        if key not in binding_keys:
            raise ValueError("PAPER_SCHEDULE_WITHOUT_APPROVED_BINDING")
        if key in schedules_by_key:
            raise ValueError("PAPER_SCHEDULE_DUPLICATE_DEFINITION")
        schedules_by_key[key] = schedule

    if set(schedules_by_key) != binding_keys:
        raise ValueError("PAPER_SCHEDULE_APPROVED_BINDING_UNSCHEDULED")

    runs: list[ScheduledJobRun] = []
    for key in sorted(schedules_by_key, key=lambda item: (item[1].value, item[2], str(item[0]))):
        schedule = schedules_by_key[key]
        calendar = calendars.get(schedule.market)
        if not isinstance(calendar, MarketSessionCalendar):
            raise ValueError("PAPER_SCHEDULE_MARKET_CALENDAR_REQUIRED")
        for session_open, session_close in calendar.sessions:
            scheduled_for = session_open + schedule.session_offset
            if scheduled_for >= session_close:
                raise ValueError("PAPER_SCHEDULE_OFFSET_OUTSIDE_SESSION")
            if window_start <= scheduled_for < window_end:
                runs.append(create_scheduled_job_run(schedule.durable_job_key, scheduled_for))

    runs.sort(key=lambda run: (run.scheduled_for, run.job_key, str(run.job_run_id)))
    return tuple(runs)


def _aware_schedule_time(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("PAPER_SCHEDULE_WINDOW_REQUIRES_DATETIME")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("PAPER_SCHEDULE_WINDOW_MUST_BE_TIMEZONE_AWARE")
    return value.astimezone(timezone.utc)


def _preflight_due_paper_job_states(
    engine: Engine,
    job_runs: Iterable[ScheduledJobRun],
) -> frozenset[UUID]:
    """Reject incomplete claims and identify terminal runs before due work executes."""
    terminal_run_ids: set[UUID] = set()
    with engine.connect() as connection:
        repository = SqlAlchemyJobRunRepository(connection)
        for job_run in job_runs:
            record = repository.get_record_for_run(job_run)
            if record is None:
                continue
            if record.status is JobRunStatus.CLAIMED:
                raise RuntimeError("PAPER_JOB_INCOMPLETE_PRIOR_CLAIM")
            terminal_run_ids.add(job_run.job_run_id)
    return frozenset(terminal_run_ids)


def run_due_operational_paper_jobs(
    engine: Engine,
    registry: PaperJobRegistry,
    job_runs: Iterable[ScheduledJobRun],
    *,
    now: Callable[[], datetime],
    max_lateness: timedelta,
) -> tuple[tuple[ScheduledJobRun, PaperCycleOutcome], ...]:
    """Execute fresh due PAPER runs in deterministic order through the authoritative runtime.

    Future runs are never claimed early. Stale nonterminal runs fail closed before any due work
    executes; already-terminal history remains an idempotent skip even when it is older than the
    replay window. Repeated invocations are safe because run_paper_once() uses the durable
    scheduled-run identity and returns SKIPPED_TERMINAL for completed work.
    """

    if not isinstance(registry, PaperJobRegistry):
        raise TypeError("PAPER_SCHEDULER_REQUIRES_JOB_REGISTRY")
    if not isinstance(max_lateness, timedelta):
        raise TypeError("PAPER_SCHEDULER_REQUIRES_MAX_LATENESS")
    if max_lateness < timedelta(0):
        raise ValueError("PAPER_SCHEDULER_MAX_LATENESS_MUST_BE_NONNEGATIVE")
    current = _aware_schedule_time(now())
    ordered: list[ScheduledJobRun] = []
    seen: set[UUID] = set()
    for job_run in job_runs:
        if not isinstance(job_run, ScheduledJobRun):
            raise TypeError("PAPER_SCHEDULER_REQUIRES_SCHEDULED_JOB_RUN")
        if job_run.job_run_id in seen:
            raise ValueError("PAPER_SCHEDULER_DUPLICATE_JOB_RUN")
        seen.add(job_run.job_run_id)
        ordered.append(job_run)

    ordered.sort(key=lambda run: (run.scheduled_for, run.job_key, str(run.job_run_id)))
    due = [job_run for job_run in ordered if job_run.scheduled_for <= current]

    for job_run in due:
        registry.resolve(job_run)

    terminal_run_ids = _preflight_due_paper_job_states(engine, due)
    if any(
        job_run.job_run_id not in terminal_run_ids
        and current - job_run.scheduled_for > max_lateness
        for job_run in due
    ):
        raise RuntimeError("PAPER_SCHEDULER_RUN_STALE")

    results: list[tuple[ScheduledJobRun, PaperCycleOutcome]] = []
    for job_run in due:
        outcome = run_paper_once(engine, job_run, registry, now=now)
        results.append((job_run, outcome))
    return tuple(results)
