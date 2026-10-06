from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Iterable
from uuid import UUID

from hope.application.jobs import JobRunStatus, ScheduledJobRun
from hope.application.paper.effect_topology import (
    PaperEffectTopology,
    classify_paper_effect_topology,
)
from hope.application.paper.effects import PaperEffectType
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository
from hope.infrastructure.scheduling.paper_lineage import verify_paper_recovery_lineage
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


def _classify_recovery_evidence(
    durable_effect_types: frozenset[PaperEffectType],
) -> PaperRecoveryEvidence:
    topology = classify_paper_effect_topology(durable_effect_types)
    return PaperRecoveryEvidence(topology.value)


def _has_unambiguous_recovery_cardinality(
    effect_counts: tuple[tuple[PaperEffectType, int], ...],
) -> bool:
    topology = classify_paper_effect_topology(
        effect_type
        for effect_type, count in effect_counts
        for _ in range(count)
    )
    return topology is PaperEffectTopology.COMPLETE


@dataclass(frozen=True)
class PaperRecoveryAssessment:
    job_run_id: UUID
    disposition: PaperRecoveryDisposition
    status: JobRunStatus | None
    durable_effect_types: frozenset[PaperEffectType] = frozenset()
    effect_counts: tuple[tuple[PaperEffectType, int], ...] = ()
    lineage_verified: bool = False

    @property
    def has_durable_effects(self) -> bool:
        return bool(self.durable_effect_types)

    @property
    def evidence(self) -> PaperRecoveryEvidence:
        if self.effect_counts:
            topology = classify_paper_effect_topology(
                effect_type
                for effect_type, count in self.effect_counts
                for _ in range(count)
            )
            return PaperRecoveryEvidence(topology.value)
        return _classify_recovery_evidence(self.durable_effect_types)

    @property
    def decision(self) -> PaperRecoveryDecision:
        if self.disposition is PaperRecoveryDisposition.FRESH:
            return PaperRecoveryDecision.EXECUTE_FRESH
        if self.disposition is PaperRecoveryDisposition.TERMINAL:
            return PaperRecoveryDecision.ACKNOWLEDGE_TERMINAL
        if self.disposition is PaperRecoveryDisposition.STALE:
            return PaperRecoveryDecision.REJECT_STALE
        if self.evidence is PaperRecoveryEvidence.COMPLETE and self.lineage_verified:
            return PaperRecoveryDecision.ACKNOWLEDGE_COMPLETE_EFFECTS
        return PaperRecoveryDecision.REQUIRE_RECONCILIATION


@dataclass(frozen=True)
class PaperRecoveryReport:
    assessments: tuple[PaperRecoveryAssessment, ...]

    @property
    def terminal_run_ids(self) -> frozenset[UUID]:
        return frozenset(item.job_run_id for item in self.assessments if item.disposition is PaperRecoveryDisposition.TERMINAL)

    @property
    def succeeded_run_ids(self) -> frozenset[UUID]:
        return frozenset(
            item.job_run_id
            for item in self.assessments
            if item.disposition is PaperRecoveryDisposition.TERMINAL
            and item.status is JobRunStatus.SUCCEEDED
        )

    @property
    def incomplete_run_ids(self) -> frozenset[UUID]:
        return frozenset(item.job_run_id for item in self.assessments if item.disposition is PaperRecoveryDisposition.INCOMPLETE)

    @property
    def incomplete_run_ids_with_effects(self) -> frozenset[UUID]:
        return frozenset(item.job_run_id for item in self.assessments if item.disposition is PaperRecoveryDisposition.INCOMPLETE and item.has_durable_effects)

    @property
    def reconciliation_run_ids(self) -> frozenset[UUID]:
        return frozenset(item.job_run_id for item in self.assessments if item.decision is PaperRecoveryDecision.REQUIRE_RECONCILIATION)

    @property
    def completed_effect_run_ids(self) -> frozenset[UUID]:
        return frozenset(item.job_run_id for item in self.assessments if item.decision is PaperRecoveryDecision.ACKNOWLEDGE_COMPLETE_EFFECTS)

    @property
    def stale_run_ids(self) -> frozenset[UUID]:
        return frozenset(item.job_run_id for item in self.assessments if item.disposition is PaperRecoveryDisposition.STALE)

    def assert_safe_to_execute(self) -> None:
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
    """Classify due PAPER work and prove filled execution lineage from durable state."""
    repository = SqlAlchemyJobRunRepository(connection)
    effects = SqlAlchemyPaperEffectRepository(connection)
    assessments: list[PaperRecoveryAssessment] = []
    for job_run in job_runs:
        record = repository.get_record_for_run(job_run)
        durable_effect_types: frozenset[PaperEffectType] = frozenset()
        effect_counts: tuple[tuple[PaperEffectType, int], ...] = ()
        lineage_verified = False
        if record is not None and record.status is JobRunStatus.CLAIMED:
            disposition = PaperRecoveryDisposition.INCOMPLETE
            run_effects = effects.list_for_job_run(job_run.job_run_id)
            counts = Counter(effect.effect_type for effect in run_effects)
            durable_effect_types = frozenset(counts)
            effect_counts = tuple(sorted(counts.items(), key=lambda item: item[0].value))
            if classify_paper_effect_topology(
                effect.effect_type for effect in run_effects
            ) is PaperEffectTopology.COMPLETE:
                lineage_verified = verify_paper_recovery_lineage(connection, run_effects)
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
                effect_counts=effect_counts,
                lineage_verified=lineage_verified,
            )
        )
    return PaperRecoveryReport(tuple(assessments))
