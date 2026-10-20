"""Bounded autonomous PAPER lifecycle built on the existing durable scheduler."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
import re
import socket
from pathlib import Path
import tempfile
from threading import Event
from types import MappingProxyType
from typing import Callable, Mapping

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import SQLAlchemyError

from hope.application.market_data.calendar import MarketSessionCalendar
from hope.domain.strategy.candidates import StrategyMarket, StrategyCandidateState
from hope.infrastructure.repositories.paper_control import SqlAlchemyPaperEnvironmentControlRepository
from hope.infrastructure.repositories.strategy_candidates import SqlAlchemyStrategyCandidateRepository
from hope.infrastructure.scheduling.paper import (
    OperationalPaperJobBinding, OperationalPaperSchedule,
    build_operational_paper_registry, build_operational_paper_runs,
    run_due_operational_paper_jobs,
)
from hope.application.paper import PaperCycleOutcome
from hope.infrastructure.durable_file import durable_replace


@dataclass(frozen=True)
class PaperServicePlan:
    """A reviewed factory supplies real data bindings and explicit session calendars."""
    bindings: tuple[OperationalPaperJobBinding, ...]
    schedules: tuple[OperationalPaperSchedule, ...]
    calendars: Mapping[StrategyMarket, MarketSessionCalendar]
    start: datetime
    end: datetime
    max_lateness: timedelta

    def __post_init__(self):
        if not isinstance(self.bindings, tuple) or not isinstance(self.schedules, tuple):
            raise ValueError("PAPER_SERVICE_PLAN_TUPLES_REQUIRED")
        if not self.bindings or len(self.bindings) > 96 or len(self.schedules) > 96:
            raise ValueError("PAPER_SERVICE_PLAN_BINDINGS_UNAVAILABLE")
        if any(not isinstance(v, OperationalPaperJobBinding) for v in self.bindings):
            raise TypeError("PAPER_SERVICE_PLAN_BINDING_INVALID")
        if any(not isinstance(v, OperationalPaperSchedule) for v in self.schedules):
            raise TypeError("PAPER_SERVICE_PLAN_SCHEDULE_INVALID")
        if any(not isinstance(v, datetime) or v.tzinfo is None or v.utcoffset() is None for v in (self.start, self.end)):
            raise ValueError("PAPER_SERVICE_PLAN_AWARE_WINDOW_REQUIRED")
        duration = self.end.astimezone(UTC) - self.start.astimezone(UTC)
        if not timedelta(0) < duration <= timedelta(days=7):
            raise ValueError("PAPER_SERVICE_PLAN_WINDOW_OUT_OF_BOUNDS")
        if not isinstance(self.max_lateness, timedelta) or not timedelta(0) <= self.max_lateness <= timedelta(days=7):
            raise ValueError("PAPER_SERVICE_PLAN_LATENESS_OUT_OF_BOUNDS")
        if not isinstance(self.calendars, Mapping):
            raise TypeError("PAPER_SERVICE_PLAN_CALENDAR_INVALID")
        # A factory-owned dict must not remain an authority after this plan has
        # passed review. Read every binding once, validate that exact value and
        # retain an immutable per-cycle snapshot through schedule construction.
        # The entry bound also prevents a custom Mapping from making validation
        # itself an unbounded operation.
        calendars: dict[StrategyMarket, MarketSessionCalendar] = {}
        session_count = 0
        for index, market in enumerate(self.calendars):
            if index >= 96:
                raise ValueError("PAPER_SERVICE_PLAN_CALENDAR_LIMIT_EXCEEDED")
            if not isinstance(market, StrategyMarket) or market in calendars:
                raise TypeError("PAPER_SERVICE_PLAN_CALENDAR_INVALID")
            calendar = self.calendars[market]
            if not isinstance(calendar, MarketSessionCalendar):
                raise TypeError("PAPER_SERVICE_PLAN_CALENDAR_INVALID")
            session_count += len(calendar.sessions)
            if session_count > 4096:
                raise ValueError("PAPER_SERVICE_PLAN_CALENDAR_LIMIT_EXCEEDED")
            calendars[market] = calendar
        object.__setattr__(self, "calendars", MappingProxyType(calendars))


PlanFactory = Callable[[Connection, datetime], PaperServicePlan]


@dataclass(frozen=True)
class SystemdNotifier:
    """Minimal main-process sd_notify client; absent outside the service unit."""

    address: str | None

    @classmethod
    def from_environment(cls) -> SystemdNotifier:
        address = os.getenv("NOTIFY_SOCKET")
        if address is None:
            return cls(None)
        if (not address or len(address) > 512 or "\0" in address or address == "@"
                or (not address.startswith("/") and not address.startswith("@"))):
            raise RuntimeError("PAPER_SERVICE_NOTIFY_CONFIGURATION_INVALID")
        watchdog_pid = os.getenv("WATCHDOG_PID")
        if watchdog_pid is not None:
            try:
                if int(watchdog_pid) != os.getpid():
                    return cls(None)
            except ValueError:
                raise RuntimeError("PAPER_SERVICE_NOTIFY_CONFIGURATION_INVALID") from None
        watchdog_usec = os.getenv("WATCHDOG_USEC")
        if watchdog_usec is None or len(watchdog_usec) > 20 or not watchdog_usec.isascii() or not watchdog_usec.isdecimal() \
                or int(watchdog_usec) <= 0:
            raise RuntimeError("PAPER_SERVICE_NOTIFY_CONFIGURATION_INVALID")
        return cls(address)

    def _send(self, value: str) -> None:
        if self.address is None:
            return
        target = "\0" + self.address[1:] if self.address.startswith("@") else self.address
        # systemd notification requires an AF_UNIX datagram socket. Keep this
        # boundary deterministic on unsupported Python/platform builds instead
        # of leaking AttributeError from a process-level health mechanism.
        family = getattr(socket, "AF_UNIX", None)
        if family is None:
            raise RuntimeError("PAPER_SERVICE_NOTIFY_FAILED")
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as client:
                # A full manager queue must not wedge status publication.
                client.settimeout(1.0)
                client.connect(target)
                client.sendall(value.encode("utf-8"))
        except OSError:
            raise RuntimeError("PAPER_SERVICE_NOTIFY_FAILED") from None

    def ready(self) -> None:
        self._send("READY=1\nWATCHDOG=1\nSTATUS=Autonomous PAPER supervisor started")

    def watchdog(self) -> None:
        self._send("WATCHDOG=1\nSTATUS=Autonomous PAPER cycle completed")

    def stopping(self) -> None:
        self._send("STOPPING=1\nSTATUS=Autonomous PAPER supervisor stopped")


def verify_schema_compatibility(connection: Connection, migrations_dir: str | Path) -> None:
    """Read-only exact migration inventory/checksum check; never repairs a schema."""
    files = sorted(Path(migrations_dir).glob("*.sql"))
    if not files:
        raise RuntimeError("PAPER_SERVICE_SCHEMA_FILES_UNAVAILABLE")
    expected = {f.name: hashlib.sha256(f.read_text(encoding="utf-8").encode("utf-8")).hexdigest() for f in files}
    rows = connection.execute(text("SELECT version,checksum FROM hope_schema_migrations")).all()
    actual = {row[0]: row[1] for row in rows}
    if len(actual) != len(rows) or actual != expected:
        raise RuntimeError("PAPER_SERVICE_SCHEMA_INCOMPATIBLE")


def run_service_cycle(engine: Engine, factory: PlanFactory, *, migrations_dir: str | Path,
                      now: Callable[[], datetime], require_certified: bool = False) -> tuple[int, int]:
    current = now()
    if not isinstance(current, datetime) or current.tzinfo is None or current.utcoffset() is None:
        raise RuntimeError("PAPER_SERVICE_CLOCK_INVALID")
    current = current.astimezone(UTC)
    with engine.connect() as connection:
        # The factory resolves configuration and persisted evidence only. Runtime
        # trading writes remain owned by the scheduler's existing transactions.
        connection.execute(text("SET TRANSACTION READ ONLY"))
        verify_schema_compatibility(connection, migrations_dir)
        SqlAlchemyPaperEnvironmentControlRepository(connection).assert_running()
        plan = factory(connection, current)
        if not isinstance(plan, PaperServicePlan):
            raise RuntimeError("PAPER_SERVICE_PLAN_UNAVAILABLE")
        candidates = SqlAlchemyStrategyCandidateRepository(connection).current()
        from hope.infrastructure.certified_paper import CertifiedResearchPaperWork
        certified_plan = all(type(binding.work) is CertifiedResearchPaperWork for binding in plan.bindings)
        if (not certified_plan and (require_certified or any(
                isinstance(binding.work, CertifiedResearchPaperWork) for binding in plan.bindings))):
            raise RuntimeError("PAPER_SERVICE_CERTIFIED_BINDING_REQUIRED")
        if certified_plan:
            current_by_version = {row.strategy_version_id: row for row in candidates}
            for binding in plan.bindings:
                work = binding.work
                candidate = current_by_version.get(binding.strategy_version_id)
                if (candidate is None or candidate.state is not StrategyCandidateState.OPERATIONAL_CANDIDATE
                        or binding.market not in candidate.markets):
                    raise RuntimeError("PAPER_STRATEGY_AUTHORIZATION_INELIGIBLE")
                if (type(work) is not CertifiedResearchPaperWork or candidate is None
                    or work.strategy_version_id != binding.strategy_version_id
                    or work.market is not binding.market
                    or work.classification_id != candidate.classification_id
                    or work.research_decision_id != candidate.research_decision_id):
                    raise RuntimeError("PAPER_SERVICE_CERTIFIED_BINDING_REQUIRED")
        registry = build_operational_paper_registry(candidates, plan.bindings)
        runs = build_operational_paper_runs(plan.bindings, plan.schedules, plan.calendars,
            start=plan.start, end=plan.end)
        if len(runs) > 256:
            raise RuntimeError("PAPER_SERVICE_RUN_LIMIT_EXCEEDED")

    def authorize_current_candidates(connection: Connection) -> None:
        # This second read is intentionally inside the scheduler's candidate
        # transition lock. The first read validates the reviewed plan; this one
        # closes the demotion-between-validation-and-claim race.
        candidates = SqlAlchemyStrategyCandidateRepository(connection).current()
        build_operational_paper_registry(candidates, plan.bindings)

    outcomes = run_due_operational_paper_jobs(
        engine,
        registry,
        runs,
        now=now,
        max_lateness=plan.max_lateness,
        # Certified bindings acquire the same candidate lock on the execution
        # connection BEFORE each claim. Holding it on this separate scheduler
        # connection would block their own bind. Generic compatibility plans
        # retain the independently published catalog revalidation contract.
        authorize=None if certified_plan else authorize_current_candidates,
    )
    if certified_plan:
        from hope.infrastructure.scheduling.paper_reconciliation import authenticate_successful_paper_run
        from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
        from hope.application.jobs import JobRunStatus
        for run, _ in outcomes:
            with engine.begin() as connection:
                durable = SqlAlchemyJobRunRepository(connection).get_record_for_run(run)
                if durable is not None and durable.status is JobRunStatus.SUCCEEDED:
                    authenticate_successful_paper_run(connection, run)
    return (sum(outcome is PaperCycleOutcome.EXECUTED for _, outcome in outcomes),
            sum(outcome is PaperCycleOutcome.SKIPPED_TERMINAL for _, outcome in outcomes))


def publish_service_status(path: str | Path, value: dict) -> None:
    """Publish one whole, private status file; interruption preserves the old file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                         prefix=f".{target.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o640)
            json.dump(value, stream, sort_keys=True, separators=(",", ":"), allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        durable_replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _failure_state(exc: Exception) -> tuple[str, str]:
    # Never serialize database errors, credentials, provider payloads or callback text.
    if isinstance(exc, RuntimeError) and str(exc) == "PAPER_ENVIRONMENT_HALTED":
        return "HALTED", "PAPER_ENVIRONMENT_HALTED"
    if isinstance(exc, (RuntimeError, ValueError)) and str(exc).startswith(
        ("PAPER_CERTIFIED_", "VERIFIED_RESEARCH_SOURCE_", "RESEARCH_RUN_EVIDENCE_", "BACKTEST_EVIDENCE_")):
        return "UNAVAILABLE", "PAPER_CERTIFIED_INPUT_UNAVAILABLE"
    if isinstance(exc, SQLAlchemyError):
        primary = getattr(getattr(getattr(exc, "orig", None), "diag", None), "message_primary", None)
        if primary in {"PAPER_STRATEGY_AUTHORIZATION_INELIGIBLE",
                       "PAPER_STRATEGY_AUTHORIZATION_RESEARCH_INVALID"}:
            return "UNAVAILABLE", primary
        return "UNAVAILABLE", "PAPER_SERVICE_DATABASE_UNAVAILABLE"
    if isinstance(exc, RuntimeError) and str(exc) in {
        "PAPER_SERVICE_SCHEMA_FILES_UNAVAILABLE", "PAPER_SERVICE_SCHEMA_INCOMPATIBLE",
        "PAPER_SERVICE_PLAN_UNAVAILABLE", "PAPER_SERVICE_RUN_LIMIT_EXCEEDED",
        "PAPER_SERVICE_CLOCK_INVALID", "PAPER_SCHEDULER_CONCURRENT_RUN",
        "PAPER_JOB_INCOMPLETE_PRIOR_CLAIM", "PAPER_MARKET_DATA_STALE",
        "PAPER_MARKET_DATA_MISSING_ACTIVE_INSTRUMENT",
        "PAPER_SERVICE_CERTIFIED_BINDING_REQUIRED",
        "PAPER_STRATEGY_AUTHORIZATION_INELIGIBLE",
    }:
        return "UNAVAILABLE", str(exc)
    return "UNAVAILABLE", "PAPER_SERVICE_CYCLE_FAILED"


def run_service(engine: Engine, factory: PlanFactory, *, migrations_dir: str | Path,
                status_file: str | Path, stop_event: Event | None = None,
                poll_seconds: float = 30, once: bool = False,
                now: Callable[[], datetime] = lambda: datetime.now(UTC),
                require_certified: bool = False,
                notifier: SystemdNotifier | None = None) -> int:
    """Poll with fresh connections; stop between cycles, never replay partial claims."""
    if isinstance(poll_seconds, bool) or not isinstance(poll_seconds, (int, float)) or not 1 <= poll_seconds <= 300:
        raise ValueError("PAPER_SERVICE_POLL_OUT_OF_BOUNDS")
    stop = stop_event if stop_event is not None else Event()
    service_notifier = notifier if notifier is not None else SystemdNotifier.from_environment()
    commit = os.getenv("HOPE_DEPLOYED_COMMIT")
    commit = commit if isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit) else None
    health = dict(deployed_commit=commit, schema_compatibility="UNKNOWN", schema_version=1, generated_at=None, state="STARTING", last_success_at=None,
                  cycles_succeeded=0, cycles_failed=0, jobs_executed=0, jobs_skipped=0,
                  error_code=None, last_failure_at=None, last_failure_code=None, paper_only=True, live_enabled=False)
    def publish():
        health["generated_at"] = datetime.now(UTC).isoformat()
        publish_service_status(status_file, health)
        print(json.dumps(health, sort_keys=True, separators=(",", ":")), flush=True)
    publish()
    ready_notified = False
    failed = False
    while not stop.is_set():
        try:
            executed, skipped = run_service_cycle(engine, factory, migrations_dir=migrations_dir, now=now,
                                                  require_certified=require_certified)
            health.update(state="CURRENT", schema_compatibility="COMPATIBLE", error_code=None, last_success_at=datetime.now(UTC).isoformat())
            health["cycles_succeeded"] += 1
            health["jobs_executed"] += executed
            health["jobs_skipped"] += skipped
            failed = False
        except Exception as exc:
            state, code = _failure_state(exc)
            health.update(last_failure_at=datetime.now(UTC).isoformat(),last_failure_code=code)
            health.update(state=state, error_code=code, schema_compatibility=("INCOMPATIBLE" if code in {"PAPER_SERVICE_SCHEMA_FILES_UNAVAILABLE", "PAPER_SERVICE_SCHEMA_INCOMPATIBLE"} else "UNKNOWN"))
            health["cycles_failed"] += 1
            failed = True
            # Broken physical sessions are discarded; a later poll rechecks the
            # schema, control state, factory and durable recovery from scratch.
            if isinstance(exc, SQLAlchemyError):
                engine.dispose()
        publish()
        # Type=notify must not make an unvalidated worker active. The first
        # READY also carries WATCHDOG=1 and is emitted only after a complete,
        # successful cycle has durably published CURRENT health. Before that,
        # systemd's startup deadline retains restart authority. Once ready,
        # every completed cycle (including a fail-closed observation) proves
        # the supervisor is responsive and may refresh the watchdog.
        if not ready_notified and not failed:
            service_notifier.ready()
            ready_notified = True
        elif ready_notified:
            service_notifier.watchdog()
        if once:
            exit_code = 1 if failed else 0
            # A successful one-shot is an acceptance probe, not a running
            # autonomous worker. Preserve its counters and last-success proof,
            # then atomically publish terminal service state before process exit.
            # Failed/HALTED probes retain their fail-closed diagnostic state.
            if not failed:
                health.update(state="STOPPED", error_code=None)
                publish()
                service_notifier.stopping()
            return exit_code
        stop.wait(poll_seconds)
    health.update(state="STOPPED", error_code=None)
    publish()
    service_notifier.stopping()
    return 0

