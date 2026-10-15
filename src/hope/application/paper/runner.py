from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Callable, Protocol
from uuid import UUID

from hope.application.jobs import (
    JobRunRecord,
    JobRunStatus,
    ScheduledJobRun,
    create_job_run_completion,
)
from hope.application.paper.context import PaperCycleContext
from hope.application.paper.fill_accounting import PaperFillAccountingWriter
from hope.application.paper.no_trade import PaperNoTradeWriter
from hope.application.paper.orders import PaperOrderWriter
from hope.application.paper.risk import PaperRiskWriter
from hope.application.paper.signals import PaperSignalWriter
from hope.application.paper.terminals import PaperTerminalOutcome, PaperTerminalWriter
from hope.domain.execution.models import ExecutionCancellation, Order
from hope.domain.execution.simulator import Fill
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.domain.signal.models import Signal


class PaperCycleOutcome(str, Enum):
    EXECUTED = "EXECUTED"
    SKIPPED_TERMINAL = "SKIPPED_TERMINAL"
    QUARANTINED_INCOMPLETE = "QUARANTINED_INCOMPLETE"


class PaperJobRunRepository(Protocol):
    def claim(self, job_run: ScheduledJobRun) -> bool:
        ...

    def complete(self, completion: JobRunRecord) -> bool:
        ...

    def get_record(self, job_run_id) -> JobRunRecord | None:
        ...


@dataclass(frozen=True)
class PaperRuntimeContext:
    """Runtime facade exposing only authoritative PAPER durable boundaries."""

    cycle: PaperCycleContext
    _signal_writer: PaperSignalWriter
    _risk_writer: PaperRiskWriter
    _order_writer: PaperOrderWriter
    _fill_writer: PaperFillAccountingWriter
    _terminal_writer: PaperTerminalWriter
    _no_trade_writer: PaperNoTradeWriter | None = None
    _environment_guard: Callable[[], None] = field(
        default=lambda: None,
        repr=False,
        compare=False,
    )
    _approved_quantities: dict[UUID, Decimal] = field(
        default_factory=dict,
        init=False,
        repr=False,
        compare=False,
    )
    _recorded_orders: dict[UUID, Order] = field(
        default_factory=dict,
        init=False,
        repr=False,
        compare=False,
    )
    _recorded_fills: dict[UUID, Fill] = field(
        default_factory=dict,
        init=False,
        repr=False,
        compare=False,
    )
    _terminal_outcomes: dict[UUID, PaperTerminalOutcome] = field(
        default_factory=dict,
        init=False,
        repr=False,
        compare=False,
    )

    def record_signal(self, signal: Signal) -> bool:
        decision_time = signal.decision_time
        if decision_time.tzinfo is None or decision_time.utcoffset() is None:
            raise ValueError("PAPER_SIGNAL_DECISION_TIME_MUST_BE_TIMEZONE_AWARE")
        if decision_time.astimezone(timezone.utc) > self.cycle.job_run.scheduled_for:
            raise ValueError("PAPER_SIGNAL_DECISION_AFTER_JOB_SCHEDULE")
        return self._signal_writer.record(self.cycle, signal)

    def record_no_trade(
        self,
        *,
        strategy_version: str,
        decision_time: datetime,
        inputs_hash: str,
    ) -> bool:
        if self._no_trade_writer is None:
            raise RuntimeError("PAPER_RUNTIME_NO_TRADE_WRITER_REQUIRED")
        return self._no_trade_writer.record(
            self.cycle,
            strategy_version=strategy_version,
            decision_time=decision_time,
            inputs_hash=inputs_hash,
        )

    def record_risk(self, assessment: RiskAssessment) -> bool:
        recorded = self._risk_writer.record(self.cycle, assessment)
        if assessment.decision is RiskDecision.APPROVE:
            self._approved_quantities[assessment.signal_id] = assessment.approved_quantity
        return recorded

    def record_order(self, order: Order) -> bool:
        self._environment_guard()
        approved_quantity = self._approved_quantities.get(order.signal_id)
        if approved_quantity is None:
            raise ValueError("PAPER_ORDER_REQUIRES_RUNTIME_RISK_APPROVAL")
        if approved_quantity != order.quantity:
            raise ValueError("PAPER_ORDER_RUNTIME_RISK_QUANTITY_MISMATCH")
        existing = self._recorded_orders.get(order.order_id)
        if existing is not None and existing != order:
            raise ValueError("PAPER_ORDER_RUNTIME_IDENTITY_CONFLICT")
        recorded = self._order_writer.record(self.cycle, order)
        self._recorded_orders[order.order_id] = order
        return recorded

    def record_terminal(self, outcome: PaperTerminalOutcome) -> bool:
        self._environment_guard()
        event_time = (
            outcome.cancellation_time
            if isinstance(outcome, ExecutionCancellation)
            else outcome.rejection_time
        )
        if event_time.astimezone(timezone.utc) > self.cycle.job_run.scheduled_for:
            raise ValueError("PAPER_TERMINAL_AFTER_JOB_SCHEDULE")
        order = self._recorded_orders.get(outcome.order_id)
        if order is not None and (
            order.signal_id != outcome.signal_id
            or order.instrument_id != outcome.instrument_id
            or order.environment is not outcome.environment
        ):
            raise ValueError("PAPER_TERMINAL_ORDER_IDENTITY_MISMATCH")
        existing = self._terminal_outcomes.get(outcome.order_id)
        if existing is not None and existing != outcome:
            raise ValueError("PAPER_ORDER_TERMINAL_CONFLICT")
        filled_quantity = sum(
            (
                fill.quantity
                for fill in self._recorded_fills.values()
                if fill.order_id == outcome.order_id
            ),
            Decimal("0"),
        )
        if order is not None:
            remaining_quantity = order.quantity - filled_quantity
            if remaining_quantity <= 0:
                raise ValueError("PAPER_TERMINAL_REQUIRES_REMAINING_QUANTITY")
            if isinstance(outcome, ExecutionCancellation):
                if outcome.cancelled_quantity != remaining_quantity:
                    raise ValueError("PAPER_CANCELLATION_QUANTITY_MISMATCH")
        if not isinstance(outcome, ExecutionCancellation) and filled_quantity != 0:
            raise ValueError("PAPER_REJECTION_REQUIRES_UNFILLED_ORDER")
        recorded = self._terminal_writer.record(self.cycle, outcome)
        self._terminal_outcomes[outcome.order_id] = outcome
        return recorded

    def record_fill(
        self,
        portfolio_id: UUID,
        initial_cash: Decimal,
        fill: Fill,
        *,
        sequence: int,
    ) -> bool:
        fill_time = fill.fill_time
        if fill_time is None or fill_time.tzinfo is None or fill_time.utcoffset() is None:
            raise ValueError("PAPER_FILL_TIME_MUST_BE_TIMEZONE_AWARE")
        if fill_time.astimezone(timezone.utc) > self.cycle.job_run.scheduled_for:
            raise ValueError("PAPER_FILL_AFTER_JOB_SCHEDULE")
        if fill.order_id in self._terminal_outcomes:
            raise ValueError("PAPER_FILL_AFTER_TERMINAL_FORBIDDEN")
        order = self._recorded_orders.get(fill.order_id)
        if order is not None and (
            order.signal_id != fill.signal_id
            or order.instrument_id != fill.instrument_id
            or order.side is not fill.side
        ):
            raise ValueError("PAPER_FILL_ORDER_IDENTITY_MISMATCH")
        existing = self._recorded_fills.get(fill.fill_id)
        if existing is not None and existing != fill:
            raise ValueError("PAPER_FILL_IDENTITY_CONFLICT")
        already_filled = sum(
            (
                recorded_fill.quantity
                for recorded_fill in self._recorded_fills.values()
                if recorded_fill.order_id == fill.order_id
                and recorded_fill.fill_id != fill.fill_id
            ),
            Decimal("0"),
        )
        if order is not None and already_filled + fill.quantity > order.quantity:
            raise ValueError("PAPER_FILL_CUMULATIVE_QUANTITY_EXCEEDS_ORDER")
        recorded = self._fill_writer.record(
            self.cycle,
            portfolio_id,
            initial_cash,
            fill,
            sequence=sequence,
        )
        self._recorded_fills[fill.fill_id] = fill
        return recorded


class PaperCycleRunner:
    """Execute one scheduled PAPER cycle behind the durable job-run boundary."""

    def __init__(
        self,
        repository: PaperJobRunRepository,
        *,
        now: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._now = now

    def _run_claimed_cycle(
        self,
        job_run: ScheduledJobRun,
        work: Callable[[PaperCycleContext], None],
    ) -> PaperCycleOutcome:
        """Internal lifecycle primitive; ordinary PAPER callers must use run_runtime()."""
        if not self._repository.claim(job_run):
            record = self._repository.get_record(job_run.job_run_id)
            if record is None:
                raise RuntimeError("PAPER_JOB_CLAIM_STATE_MISSING")
            if record.status is JobRunStatus.CLAIMED:
                raise RuntimeError("PAPER_JOB_INCOMPLETE_PRIOR_CLAIM")
            return PaperCycleOutcome.SKIPPED_TERMINAL

        context = PaperCycleContext(job_run)
        try:
            work(context)
        except Exception as exc:
            completion = create_job_run_completion(
                job_run,
                JobRunStatus.FAILED,
                self._now(),
                failure_code=type(exc).__name__,
            )
            if not self._repository.complete(completion):
                raise RuntimeError("PAPER_JOB_COMPLETION_CONFLICT") from exc
            raise

        completion = create_job_run_completion(
            job_run,
            JobRunStatus.SUCCEEDED,
            self._now(),
        )
        if not self._repository.complete(completion):
            raise RuntimeError("PAPER_JOB_COMPLETION_CONFLICT")
        return PaperCycleOutcome.EXECUTED

    def run_runtime(
        self,
        job_run: ScheduledJobRun,
        signal_writer: PaperSignalWriter,
        risk_writer: PaperRiskWriter,
        order_writer: PaperOrderWriter,
        fill_writer: PaperFillAccountingWriter,
        work: Callable[[PaperRuntimeContext], None],
        *,
        terminal_writer: PaperTerminalWriter,
        no_trade_writer: PaperNoTradeWriter | None = None,
    ) -> PaperCycleOutcome:
        """Run ordinary PAPER work through separate authoritative durable boundaries."""
        if not isinstance(signal_writer, PaperSignalWriter):
            raise TypeError("PAPER_RUNTIME_REQUIRES_AUTHORITATIVE_SIGNAL_WRITER")
        if not isinstance(risk_writer, PaperRiskWriter):
            raise TypeError("PAPER_RUNTIME_REQUIRES_AUTHORITATIVE_RISK_WRITER")
        if not isinstance(order_writer, PaperOrderWriter):
            raise TypeError("PAPER_RUNTIME_REQUIRES_AUTHORITATIVE_ORDER_WRITER")
        if not isinstance(fill_writer, PaperFillAccountingWriter):
            raise TypeError("PAPER_RUNTIME_REQUIRES_AUTHORITATIVE_FILL_WRITER")
        if not isinstance(terminal_writer, PaperTerminalWriter):
            raise TypeError("PAPER_RUNTIME_REQUIRES_AUTHORITATIVE_TERMINAL_WRITER")
        return self._run_claimed_cycle(
            job_run,
            lambda context: work(
                PaperRuntimeContext(
                    context,
                    signal_writer,
                    risk_writer,
                    order_writer,
                    fill_writer,
                    terminal_writer,
                    no_trade_writer,
                )
            ),
        )

    def quarantine_incomplete_claim(self, job_run: ScheduledJobRun) -> PaperCycleOutcome:
        """Explicitly terminalize a stranded claim after the caller proves its worker is gone."""
        record = self._repository.get_record(job_run.job_run_id)
        if record is None:
            raise RuntimeError("PAPER_JOB_CLAIM_STATE_MISSING")
        if record.status is not JobRunStatus.CLAIMED:
            return PaperCycleOutcome.SKIPPED_TERMINAL

        completion = create_job_run_completion(
            job_run,
            JobRunStatus.FAILED,
            self._now(),
            failure_code="PAPER_JOB_INCOMPLETE_PRIOR_CLAIM",
        )
        if not self._repository.complete(completion):
            raise RuntimeError("PAPER_JOB_COMPLETION_CONFLICT")
        return PaperCycleOutcome.QUARANTINED_INCOMPLETE
