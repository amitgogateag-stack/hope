from __future__ import annotations
from datetime import timezone
from hashlib import sha256
from typing import Protocol
from hope.application.paper.context import PaperCycleContext
from hope.application.paper.effects import PaperEffect, PaperEffectType, create_paper_effect
from hope.domain.execution.models import Environment, ExecutionCancellation, ExecutionRejection
PaperTerminalOutcome = ExecutionCancellation | ExecutionRejection
def paper_terminal_payload_hash(outcome: PaperTerminalOutcome) -> str:
    if outcome.environment is not Environment.PAPER: raise ValueError("PAPER_TERMINAL_REQUIRES_PAPER_ENVIRONMENT")
    if isinstance(outcome, ExecutionCancellation):
        values=(str(outcome.order_id),str(outcome.signal_id),str(outcome.instrument_id),"CANCELLED",outcome.reason_code,outcome.cancellation_time.astimezone(timezone.utc).isoformat(),format(outcome.cancelled_quantity.normalize(),"f"))
    else: values=(str(outcome.order_id),str(outcome.signal_id),str(outcome.instrument_id),"REJECTED",outcome.reason_code,outcome.rejection_time.astimezone(timezone.utc).isoformat())
    return sha256("|".join(values).encode()).hexdigest()
class PaperTerminalPersistence(Protocol):
    def persist(self,effect:PaperEffect,outcome:PaperTerminalOutcome)->bool: ...
class PaperTerminalWriter:
    def __init__(self,repository:PaperTerminalPersistence)->None: self._repository=repository
    def record(self,context:PaperCycleContext,outcome:PaperTerminalOutcome)->bool:
        if outcome.environment is not Environment.PAPER: raise ValueError("PAPER_TERMINAL_REQUIRES_PAPER_ENVIRONMENT")
        event_time=outcome.cancellation_time if isinstance(outcome,ExecutionCancellation) else outcome.rejection_time
        if event_time.astimezone(timezone.utc) > context.job_run.scheduled_for: raise ValueError("PAPER_TERMINAL_AFTER_JOB_SCHEDULE")
        if outcome.order_id != context.order_id(outcome.signal_id): raise ValueError("PAPER_TERMINAL_ORDER_IDENTITY_MISMATCH")
        kind=PaperEffectType.CANCELLATION if isinstance(outcome,ExecutionCancellation) else PaperEffectType.REJECTION
        return self._repository.persist(create_paper_effect(context.job_run,kind,outcome.order_id,paper_terminal_payload_hash(outcome)),outcome)
