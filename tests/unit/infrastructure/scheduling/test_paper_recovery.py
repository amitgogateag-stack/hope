from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from hope.application.jobs import JobRunStatus, create_scheduled_job_run
from hope.application.paper.effects import PaperEffectType
from hope.infrastructure.scheduling import recovery
from hope.infrastructure.scheduling.recovery import (
    PaperRecoveryAssessment,
    PaperRecoveryDecision,
    PaperRecoveryDisposition,
    PaperRecoveryEvidence,
    PaperRecoveryReport,
    _classify_recovery_evidence,
    assess_due_paper_recovery,
)


def test_recovery_assessment_classifies_full_restart_matrix(monkeypatch) -> None:
    current = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
    fresh = create_scheduled_job_run("paper-fresh", current - timedelta(minutes=1))
    terminal = create_scheduled_job_run("paper-terminal", current - timedelta(minutes=20))
    incomplete = create_scheduled_job_run("paper-incomplete", current - timedelta(minutes=2))
    stale = create_scheduled_job_run("paper-stale", current - timedelta(minutes=6))

    records = {
        terminal.job_run_id: SimpleNamespace(status=JobRunStatus.SUCCEEDED),
        incomplete.job_run_id: SimpleNamespace(status=JobRunStatus.CLAIMED),
    }

    class FakeRepository:
        def __init__(self, connection):
            self.connection = connection

        def get_record_for_run(self, job_run):
            return records.get(job_run.job_run_id)

    class FakeEffectRepository:
        def __init__(self, connection):
            self.connection = connection

        def list_for_job_run(self, job_run_id):
            if job_run_id == incomplete.job_run_id:
                return (
                    SimpleNamespace(effect_type=PaperEffectType.SIGNAL),
                    SimpleNamespace(effect_type=PaperEffectType.ORDER),
                )
            return ()

    monkeypatch.setattr(recovery, "SqlAlchemyJobRunRepository", FakeRepository)
    monkeypatch.setattr(recovery, "SqlAlchemyPaperEffectRepository", FakeEffectRepository)
    report = assess_due_paper_recovery(
        object(),
        [fresh, terminal, incomplete, stale],
        current=current,
        max_lateness=timedelta(minutes=5),
    )

    dispositions = {item.job_run_id: item.disposition for item in report.assessments}
    assert dispositions == {
        fresh.job_run_id: PaperRecoveryDisposition.FRESH,
        terminal.job_run_id: PaperRecoveryDisposition.TERMINAL,
        incomplete.job_run_id: PaperRecoveryDisposition.INCOMPLETE,
        stale.job_run_id: PaperRecoveryDisposition.STALE,
    }
    decisions = {item.job_run_id: item.decision for item in report.assessments}
    assert decisions == {
        fresh.job_run_id: PaperRecoveryDecision.EXECUTE_FRESH,
        terminal.job_run_id: PaperRecoveryDecision.ACKNOWLEDGE_TERMINAL,
        incomplete.job_run_id: PaperRecoveryDecision.REQUIRE_RECONCILIATION,
        stale.job_run_id: PaperRecoveryDecision.REJECT_STALE,
    }
    assert report.terminal_run_ids == frozenset({terminal.job_run_id})
    assert report.incomplete_run_ids == frozenset({incomplete.job_run_id})
    assert report.incomplete_run_ids_with_effects == frozenset({incomplete.job_run_id})
    assert report.reconciliation_run_ids == frozenset({incomplete.job_run_id})
    assert report.completed_effect_run_ids == frozenset()
    assert report.stale_run_ids == frozenset({stale.job_run_id})
    incomplete_assessment = next(
        item for item in report.assessments if item.job_run_id == incomplete.job_run_id
    )
    assert incomplete_assessment.durable_effect_types == frozenset(
        {PaperEffectType.SIGNAL, PaperEffectType.ORDER}
    )
    assert incomplete_assessment.effect_counts == (
        (PaperEffectType.ORDER, 1),
        (PaperEffectType.SIGNAL, 1),
    )
    assert incomplete_assessment.lineage_verified is False
    assert incomplete_assessment.evidence is PaperRecoveryEvidence.PARTIAL


def test_recovery_evidence_classifies_none_partial_complete_and_contradictory() -> None:
    assert _classify_recovery_evidence(frozenset()) is PaperRecoveryEvidence.NONE
    assert _classify_recovery_evidence(
        frozenset({PaperEffectType.SIGNAL, PaperEffectType.ORDER})
    ) is PaperRecoveryEvidence.PARTIAL
    assert _classify_recovery_evidence(
        frozenset(
            {
                PaperEffectType.SIGNAL,
                PaperEffectType.RISK,
                PaperEffectType.ORDER,
                PaperEffectType.FILL,
                PaperEffectType.PNL,
            }
        )
    ) is PaperRecoveryEvidence.COMPLETE
    assert _classify_recovery_evidence(
        frozenset(
            {
                PaperEffectType.SIGNAL,
                PaperEffectType.RISK,
                PaperEffectType.ORDER,
                PaperEffectType.REJECTION,
            }
        )
    ) is PaperRecoveryEvidence.COMPLETE
    assert _classify_recovery_evidence(
        frozenset(
            {
                PaperEffectType.SIGNAL,
                PaperEffectType.RISK,
                PaperEffectType.ORDER,
                PaperEffectType.FILL,
                PaperEffectType.REJECTION,
            }
        )
    ) is PaperRecoveryEvidence.CONTRADICTORY


def _complete_assessment(*, lineage_verified: bool) -> PaperRecoveryAssessment:
    return PaperRecoveryAssessment(
        job_run_id=uuid4(),
        disposition=PaperRecoveryDisposition.INCOMPLETE,
        status=JobRunStatus.CLAIMED,
        durable_effect_types=frozenset(
            {
                PaperEffectType.SIGNAL,
                PaperEffectType.RISK,
                PaperEffectType.ORDER,
                PaperEffectType.FILL,
                PaperEffectType.PNL,
            }
        ),
        effect_counts=(
            (PaperEffectType.FILL, 1),
            (PaperEffectType.ORDER, 1),
            (PaperEffectType.PNL, 1),
            (PaperEffectType.RISK, 1),
            (PaperEffectType.SIGNAL, 1),
        ),
        lineage_verified=lineage_verified,
    )


def test_complete_effect_types_without_lineage_proof_require_reconciliation() -> None:
    complete = _complete_assessment(lineage_verified=False)
    report = PaperRecoveryReport((complete,))

    assert complete.evidence is PaperRecoveryEvidence.COMPLETE
    assert complete.decision is PaperRecoveryDecision.REQUIRE_RECONCILIATION
    assert report.completed_effect_run_ids == frozenset()
    assert report.reconciliation_run_ids == frozenset({complete.job_run_id})
    with pytest.raises(RuntimeError, match="PAPER_JOB_INCOMPLETE_PRIOR_CLAIM"):
        report.assert_safe_to_execute()


def test_complete_effects_are_acknowledged_only_after_lineage_proof() -> None:
    complete = _complete_assessment(lineage_verified=True)
    report = PaperRecoveryReport((complete,))

    assert complete.evidence is PaperRecoveryEvidence.COMPLETE
    assert complete.decision is PaperRecoveryDecision.ACKNOWLEDGE_COMPLETE_EFFECTS
    assert report.completed_effect_run_ids == frozenset({complete.job_run_id})
    assert report.reconciliation_run_ids == frozenset()
    with pytest.raises(RuntimeError, match="PAPER_JOB_INCOMPLETE_PRIOR_CLAIM"):
        report.assert_safe_to_execute()


def test_duplicate_effect_type_requires_reconciliation_even_when_type_set_is_complete() -> None:
    job_run_id = uuid4()
    ambiguous = PaperRecoveryAssessment(
        job_run_id=job_run_id,
        disposition=PaperRecoveryDisposition.INCOMPLETE,
        status=JobRunStatus.CLAIMED,
        durable_effect_types=frozenset(
            {
                PaperEffectType.SIGNAL,
                PaperEffectType.RISK,
                PaperEffectType.ORDER,
                PaperEffectType.FILL,
                PaperEffectType.PNL,
            }
        ),
        effect_counts=(
            (PaperEffectType.FILL, 2),
            (PaperEffectType.ORDER, 1),
            (PaperEffectType.PNL, 1),
            (PaperEffectType.RISK, 1),
            (PaperEffectType.SIGNAL, 1),
        ),
        lineage_verified=True,
    )
    report = PaperRecoveryReport((ambiguous,))

    assert ambiguous.evidence is PaperRecoveryEvidence.CONTRADICTORY
    assert ambiguous.decision is PaperRecoveryDecision.REQUIRE_RECONCILIATION
    assert report.completed_effect_run_ids == frozenset()
    assert report.reconciliation_run_ids == frozenset({job_run_id})


def test_recovery_report_fails_closed_on_incomplete_before_stale(monkeypatch) -> None:
    current = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
    incomplete = create_scheduled_job_run("paper-incomplete", current - timedelta(minutes=2))
    stale = create_scheduled_job_run("paper-stale", current - timedelta(minutes=6))

    class FakeRepository:
        def __init__(self, connection):
            pass

        def get_record_for_run(self, job_run):
            if job_run.job_run_id == incomplete.job_run_id:
                return SimpleNamespace(status=JobRunStatus.CLAIMED)
            return None

    class FakeEffectRepository:
        def __init__(self, connection):
            pass

        def list_for_job_run(self, job_run_id):
            return ()

    monkeypatch.setattr(recovery, "SqlAlchemyJobRunRepository", FakeRepository)
    monkeypatch.setattr(recovery, "SqlAlchemyPaperEffectRepository", FakeEffectRepository)
    report = assess_due_paper_recovery(
        object(),
        [stale, incomplete],
        current=current,
        max_lateness=timedelta(minutes=5),
    )

    assert report.incomplete_run_ids_with_effects == frozenset()
    incomplete_assessment = next(
        item for item in report.assessments if item.job_run_id == incomplete.job_run_id
    )
    assert incomplete_assessment.evidence is PaperRecoveryEvidence.NONE
    assert incomplete_assessment.decision is PaperRecoveryDecision.REQUIRE_RECONCILIATION
    with pytest.raises(RuntimeError, match="PAPER_JOB_INCOMPLETE_PRIOR_CLAIM"):
        report.assert_safe_to_execute()
