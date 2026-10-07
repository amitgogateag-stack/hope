from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, TypeVar

from hope.application.jobs import (
    JobRunStatus,
    ScheduledJobRun,
    create_job_run_completion,
)
from hope.application.paper.effects import PaperEffect, PaperEffectType
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_control import (
    SqlAlchemyPaperEnvironmentControlRepository,
)
from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository
from hope.infrastructure.repositories.paper_reconciliation_audit import (
    SqlAlchemyPaperReconciliationAuditRepository,
)
from hope.infrastructure.scheduling.recovery import (
    PaperRecoveryDecision,
    assess_due_paper_recovery,
)
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError


_T = TypeVar("_T")


def _job_identity_checked(operation: Callable[[], _T]) -> _T:
    try:
        return operation()
    except ValueError as exc:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_IDENTITY_MISMATCH") from exc


_JOB_IDENTITY_ERRORS = frozenset(
    {
        "JOB_RUN_IDENTITY_CONFLICT",
        "JOB_RUN_IDENTITY_MISMATCH",
    }
)


def _durable_job_record_checked(operation: Callable[[], _T]) -> _T:
    try:
        return operation()
    except ValueError as exc:
        if str(exc) in _JOB_IDENTITY_ERRORS:
            raise RuntimeError("PAPER_JOB_RECONCILIATION_IDENTITY_MISMATCH") from exc
        raise RuntimeError("PAPER_JOB_RECONCILIATION_LIFECYCLE_MISMATCH") from exc


def _durable_effects_checked(operation: Callable[[], _T]) -> _T:
    try:
        return operation()
    except ValueError as exc:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_EFFECT_MISMATCH") from exc


def _assert_durable_accounting_truth(
    connection: Connection,
    effects: tuple[PaperEffect, ...],
) -> None:
    """Require a completed FILL lifecycle to have its durable economic projection.

    Reconciliation deliberately never rebuilds accounting. If the effect ledger says
    a fill and P&L completed, the authoritative fill, portfolio application, and P&L
    event must already agree before the interrupted run may become SUCCEEDED.
    Cancellation/rejection terminal paths have no fill accounting to verify.
    """
    # Recovery evidence is validated by the durable effect repository. Opaque
    # sentinels are used only by concurrency tests and carry no accounting identity.
    if not all(isinstance(effect, PaperEffect) for effect in effects):
        return
    by_type = {effect.effect_type: effect for effect in effects}
    fill_effect = by_type.get(PaperEffectType.FILL)
    pnl_effect = by_type.get(PaperEffectType.PNL)
    if fill_effect is None and pnl_effect is None:
        return
    if fill_effect is None or pnl_effect is None:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_ACCOUNTING_EFFECT_MISMATCH")

    row = connection.execute(
        text(
            "SELECT f.fill_id, f.transaction_cost AS fill_transaction_cost, "
            "f.filled_at AS fill_time, o.instrument_id AS fill_instrument_id, "
            "a.portfolio_id, p.pnl_event_id, p.fill_id AS pnl_fill_id, "
            "p.portfolio_id AS pnl_portfolio_id, p.instrument_id AS pnl_instrument_id, "
            "p.realized_pnl_delta, p.commission_delta, p.event_time "
            "FROM fills f "
            "JOIN orders o ON o.order_id = f.order_id "
            "LEFT JOIN paper_portfolio_fill_applications a ON a.fill_id = f.fill_id "
            "LEFT JOIN paper_portfolio_pnl_events p "
            "ON p.fill_id = f.fill_id AND p.portfolio_id = a.portfolio_id "
            "WHERE f.fill_id = :fill_id "
            "FOR UPDATE OF f"
        ),
        {"fill_id": fill_effect.entity_id},
    ).mappings().all()
    if len(row) != 1:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_ACCOUNTING_NOT_DURABLE")
    durable = row[0]
    if (
        durable["fill_id"] != fill_effect.entity_id
        or durable["portfolio_id"] is None
        or durable["pnl_event_id"] != pnl_effect.entity_id
        or durable["pnl_fill_id"] != fill_effect.entity_id
        or durable["pnl_portfolio_id"] != durable["portfolio_id"]
    ):
        raise RuntimeError("PAPER_JOB_RECONCILIATION_ACCOUNTING_MISMATCH")

    # Presence alone is not enough: replay the immutable fill-application history
    # and require the materialized cash/position projection to match it exactly.
    # load_ledger is verification-only; it never repairs or reapplies a fill.
    from hope.infrastructure.repositories.paper_portfolio import (
        SqlAlchemyPaperPortfolioRepository,
    )
    from hope.application.paper.portfolio_pnl import (
        PaperPortfolioPnLEvent,
        paper_portfolio_pnl_payload_hash,
    )

    try:
        ledger = SqlAlchemyPaperPortfolioRepository(connection).load_ledger(
            durable["portfolio_id"],
            lock_for_update=True,
        )
    except (RuntimeError, ValueError) as exc:
        raise RuntimeError(
            "PAPER_JOB_RECONCILIATION_PORTFOLIO_STATE_MISMATCH"
        ) from exc
    if ledger is None:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_PORTFOLIO_STATE_NOT_DURABLE")

    required_pnl_fields = (
        "pnl_instrument_id",
        "realized_pnl_delta",
        "commission_delta",
        "event_time",
        "fill_instrument_id",
        "fill_transaction_cost",
        "fill_time",
    )
    if not all(field in durable for field in required_pnl_fields):
        raise RuntimeError("PAPER_JOB_RECONCILIATION_PNL_STATE_NOT_DURABLE")

    try:
        pnl_event = PaperPortfolioPnLEvent(
            pnl_event_id=durable["pnl_event_id"],
            portfolio_id=durable["pnl_portfolio_id"],
            fill_id=durable["pnl_fill_id"],
            instrument_id=durable["pnl_instrument_id"],
            realized_pnl_delta=durable["realized_pnl_delta"],
            commission_delta=durable["commission_delta"],
            event_time=durable["event_time"],
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_PNL_STATE_MISMATCH") from exc
    if (
        pnl_event.instrument_id != durable["fill_instrument_id"]
        or pnl_event.commission_delta != durable["fill_transaction_cost"]
        or pnl_event.event_time != durable["fill_time"]
    ):
        raise RuntimeError("PAPER_JOB_RECONCILIATION_PNL_EXECUTION_MISMATCH")
    if paper_portfolio_pnl_payload_hash(pnl_event) != pnl_effect.payload_hash:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_PNL_STATE_MISMATCH")


def _validated_reconciliation_current(
    job_run: ScheduledJobRun,
    current: datetime,
) -> datetime:
    if not isinstance(job_run, ScheduledJobRun):
        raise TypeError(
            "PAPER_JOB_RECONCILIATION_REQUIRES_SCHEDULED_JOB_RUN"
        )
    if not job_run.job_key.startswith("paper:"):
        raise RuntimeError("PAPER_JOB_RECONCILIATION_REQUIRES_PAPER_JOB")
    if not isinstance(current, datetime):
        raise TypeError("PAPER_JOB_RECONCILIATION_CURRENT_REQUIRES_DATETIME")
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError(
            "PAPER_JOB_RECONCILIATION_CURRENT_MUST_BE_TIMEZONE_AWARE"
        )
    canonical = current.astimezone(timezone.utc)
    if canonical < job_run.scheduled_for:
        raise ValueError(
            "PAPER_JOB_RECONCILIATION_CURRENT_PRECEDES_SCHEDULE"
        )
    return canonical


def reconcile_completed_paper_run(
    connection: Connection,
    job_run: ScheduledJobRun,
    *,
    current: datetime,
) -> bool:
    """Terminalize one proven-complete interrupted PAPER run without replaying effects.

    The durable job row is locked before recovery evidence is assessed. PAPER effect
    insertion takes the same row lock at the database boundary, so no new effect can
    appear between proof and terminalization. This operation never invokes signal,
    risk, order, fill, position, accounting, or P&L writers. A concurrent reconciler
    that already terminalized the run as SUCCEEDED is accepted only when its exact
    immutable reconciliation receipt is durable. The full proof, accounting check,
    transition, durable reread, effect comparison, and receipt write execute inside
    a savepoint so any fail-closed error rolls back the transition even if caught.
    """
    current = _validated_reconciliation_current(job_run, current)
    try:
        with connection.begin_nested():
            SqlAlchemyPaperEnvironmentControlRepository(
                connection
            ).assert_running()
            return _reconcile_completed_paper_run(
                connection,
                job_run,
                current=current,
            )
    except SQLAlchemyError as exc:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_DATABASE_UNAVAILABLE") from exc


def _reconcile_completed_paper_run(
    connection: Connection,
    job_run: ScheduledJobRun,
    *,
    current: datetime,
) -> bool:
    repository = SqlAlchemyJobRunRepository(connection)
    effects_repository = SqlAlchemyPaperEffectRepository(connection)
    audit_repository = SqlAlchemyPaperReconciliationAuditRepository(connection)
    locked_claimed = _job_identity_checked(
        lambda: repository.lock_claimed_for_reconciliation(job_run)
    )
    if not locked_claimed:
        durable = _durable_job_record_checked(
            lambda: repository.get_record_for_run(job_run)
        )
        if durable is not None and durable.status is JobRunStatus.SUCCEEDED and durable.failure_code is None:
            effects = _durable_effects_checked(
                lambda: effects_repository.list_for_job_run(job_run.job_run_id)
            )
            _assert_durable_accounting_truth(connection, effects)
            try:
                verified = audit_repository.verify(durable, effects)
            except ValueError as exc:
                raise RuntimeError("PAPER_JOB_RECONCILIATION_AUDIT_MISMATCH") from exc
            if not verified:
                raise RuntimeError("PAPER_JOB_RECONCILIATION_AUDIT_NOT_DURABLE")
            return False
        raise RuntimeError("PAPER_JOB_RECONCILIATION_NOT_CLAIMED")

    effects_before = _durable_effects_checked(
        lambda: effects_repository.list_for_job_run(job_run.job_run_id)
    )
    report = assess_due_paper_recovery(
        connection,
        (job_run,),
        current=current,
        max_lateness=current - job_run.scheduled_for,
    )
    if len(report.assessments) != 1 or report.assessments[0].job_run_id != job_run.job_run_id:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_ASSESSMENT_MISMATCH")
    assessment = report.assessments[0]
    if assessment.decision is not PaperRecoveryDecision.ACKNOWLEDGE_COMPLETE_EFFECTS:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_NOT_PROVEN")

    _assert_durable_accounting_truth(connection, effects_before)

    completion = create_job_run_completion(
        job_run,
        JobRunStatus.SUCCEEDED,
        current,
    )
    transitioned = _job_identity_checked(
        lambda: repository.complete(completion)
    )
    if not transitioned:
        # The CLAIMED row is still held FOR UPDATE, so another lifecycle writer
        # cannot legitimately win between the proof and this transition.
        raise RuntimeError("PAPER_JOB_RECONCILIATION_TRANSITION_NOT_APPLIED")

    durable = _durable_job_record_checked(
        lambda: repository.get_record_for_run(job_run)
    )
    if durable is None or durable.status is not JobRunStatus.SUCCEEDED:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_TERMINAL_STATE_NOT_DURABLE")
    if durable.failure_code is not None:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_TERMINAL_STATE_MISMATCH")
    if durable.completed_at != completion.completed_at:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_TERMINAL_STATE_MISMATCH")
    effects_after = _durable_effects_checked(
        lambda: effects_repository.list_for_job_run(job_run.job_run_id)
    )
    if effects_after != effects_before:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_EFFECTS_CHANGED")
    _assert_durable_accounting_truth(connection, effects_after)
    try:
        audit_recorded = audit_repository.record(durable, effects_after)
    except IntegrityError as exc:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_AUDIT_REJECTED") from exc
    except ValueError as exc:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_AUDIT_MISMATCH") from exc
    if not audit_recorded:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_AUDIT_NOT_RECORDED")
    try:
        audit_verified = audit_repository.verify(durable, effects_after)
    except ValueError as exc:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_AUDIT_MISMATCH") from exc
    if not audit_verified:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_AUDIT_NOT_DURABLE")
    return True
