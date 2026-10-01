from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Iterable
from uuid import UUID

from hope.application.jobs import JobRunStatus, ScheduledJobRun
from hope.application.paper.effects import PaperEffectType
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository
from sqlalchemy import Connection


class PaperRecoveryDisposition(str, Enum):
    FRESH = "FRESH"
    TERMINAL = "TERMINAL"
    INCOMPLETE = "INCOMPLETE"
    STALE = "STALE"


class PaperRecoveryEvidence(str, Enum):
    NONE = "NONE"
    PARTIAL = "PARTIAL"
    COMPLETE = "COMPLETE"
    CONTRADICTORY = "CONTRADICTORY"


class PaperRecoveryDecision(str, Enum):
    EXECUTE_FRESH = "EXECUTE_FRESH"
    ACKNOWLEDGE_TERMINAL = "ACKNOWLEDGE_TERMINAL"
    ACKNOWLEDGE_COMPLETE_EFFECTS = "ACKNOWLEDGE_COMPLETE_EFFECTS"
    REQUIRE_RECONCILIATION = "REQUIRE_RECONCILIATION"
    REJECT_STALE = "REJECT_STALE"


_TERMINAL_EFFECT_TYPES = frozenset(
    {
        PaperEffectType.FILL,
        PaperEffectType.CANCELLATION,
        PaperEffectType.REJECTION,
    }
)


def _classify_recovery_evidence(
    durable_effect_types: frozenset[PaperEffectType],
) -> PaperRecoveryEvidence:
    if not durable_effect_types:
        return PaperRecoveryEvidence.NONE

    terminal_types = durable_effect_types & _TERMINAL_EFFECT_TYPES
    if len(terminal_types) > 1:
        return PaperRecoveryEvidence.CONTRADICTORY

    if terminal_types:
        terminal_type = next(iter(terminal_types))
        if terminal_type is PaperEffectType.FILL:
            required = frozenset(
                {
                    PaperEffectType.SIGNAL,
                    PaperEffectType.RISK,
                    PaperEffectType.ORDER,
                    PaperEffectType.FILL,
                    PaperEffectType.PNL,
                }
            )
        else:
            required = frozenset(
                {
                    PaperEffectType.SIGNAL,
                    PaperEffectType.RISK,
                    PaperEffectType.ORDER,
                    terminal_type,
                }
            )
        if required <= durable_effect_types:
            return PaperRecoveryEvidence.COMPLETE

    return PaperRecoveryEvidence.PARTIAL


@dataclass(frozen=True)
class PaperRecoveryAssessment:
    job_run_id: UUID
    disposition: PaperRecoveryDisposition
    status: JobRunStatus | None
    durable_effect_types: frozenset[PaperEffectType] = frozenset()

    @property
    def has_durable_effects(self) -> bool:
        return bool(self.durable_effect_types)

    @property
    def evidence(self) -> PaperRecoveryEvidence:
        return _classify_recovery_evidence(self.durable_effect_types)

    @property
    def decision(self) -> PaperRecoveryDecision:
        if self.disposition is PaperRecoveryDisposition.FRESH:
            return PaperRecoveryDecision.EXECUTE_FRESH
        if self.disposition is PaperRecoveryDisposition.TERMINAL:
            return PaperRecoveryDecision.ACKNOWLEDGE_TERMINAL
        if self.disposition is PaperRecoveryDisposition.STALE:
            return PaperRecoveryDecision.REJECT_STALE
        if self.evidence is PaperRecoveryEvidence.COMPLETE:
            return PaperRecoveryDecision.ACKNOWLEDGE_COMPLETE_EFFECTS
        return PaperRecoveryDecision.REQUIRE_RECONCILIATION


@dataclass(frozen=True)
class PaperRecoveryReport:
    assessments: tuple[PaperRecoveryAssessment, ...]

    @property
    def terminal_run_ids(self) -> frozenset[UUID]:
        return frozenset(
            item.job_run_id
            for item in self.assessments
            if item.disposition is PaperRecoveryDisposition.TERMINAL
        )

    @property
    def incomplete_run_ids(self) -> frozenset[UUID]:
        return frozenset(
            item.job_run_id
            for item in self.assessments
            if item.disposition is PaperRecoveryDisposition.INCOMPLETE
        )

    @property
    def incomplete_run_ids_with_effects(self) -> frozenset[UUID]:
        return frozenset(
            item.job_run_id
            for item in self.assessments
            if item.disposition is PaperRecoveryDisposition.INCOMPLETE and item.has_durable_effects
        )

    @property
    def reconciliation_run_ids(self) -> frozenset[UUID]:
        return frozenset(
            item.job_run_id
            for item in self.assessments
            if item.decision is PaperRecoveryDecision.REQUIRE_RECONCILIATION
        )

    @property
    def completed_effect_run_ids(self) -> frozenset[UUID]:
        return frozenset(
            item.job_run_id
            for item in self.assessments
            if item.decision is PaperRecoveryDecision.ACKNOWLEDGE_COMPLETE_EFFECTS
        )

    @property
    def stale_run_ids(self) -> frozenset[UUID]:
        return frozenset(
            item.job_run_id
            for item in self.assessments
            if item.disposition is PaperRecoveryDisposition.STALE
        )

    def assert_safe_to_execute(self) -> None:
        # Complete durable effects are recognized as economically complete, but a CLAIMED
        # lifecycle row is still not silently mutated here. Scheduler execution remains
        # fail-closed until a separate reconciliation transition is proven safe and durable.
        if self.incomplete_run_ids:
            raise RuntimeError("PAPER_JOB_INCOMPLETE_PRIOR_CLAIM")
        if self.stale_run_ids:
            raise RuntimeError("PAPER_SCHEDULER_RUN_STALE")


def assess_due_paper_recovery(
    connection: Connection,
    job_runs: Iterable[ScheduledJobRun],
    *,
    current: datetime,
    max_lateness: timedelta,
) -> PaperRecoveryReport:
    """Classify all due PAPER work from durable truth before any execution begins."""
    repository = SqlAlchemyJobRunRepository(connection)
    effects = SqlAlchemyPaperEffectRepository(connection)
    assessments: list[PaperRecoveryAssessment] = []
    for job_run in job_runs:
        record = repository.get_record_for_run(job_run)
        durable_effect_types: frozenset[PaperEffectType] = frozenset()
        if record is not None and record.status is JobRunStatus.CLAIMED:
            disposition = PaperRecoveryDisposition.INCOMPLETE
            durable_effect_types = frozenset(
                effect.effect_type for effect in effects.list_for_job_run(job_run.job_run_id)
            )
        elif record is not None:
            disposition = PaperRecoveryDisposition.TERMINAL
        elif current - job_run.scheduled_for > max_lateness:
            disposition = PaperRecoveryDisposition.STALE
        else:
            disposition = PaperRecoveryDisposition.FRESH
        assessments.append(
            PaperRecoveryAssessment(
                job_run_id=job_run.job_run_id,
                disposition=disposition,
                status=record.status if record is not None else None,
                durable_effect_types=durable_effect_types,
            )
        )
    return PaperRecoveryReport(tuple(assessments))
