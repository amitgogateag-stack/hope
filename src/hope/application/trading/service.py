from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5

from hope.domain.audit.models import AuditEvent, AuditEventType
from hope.domain.audit.validator import validate_audit_sequence
from hope.domain.execution.lifecycle import OrderLifecycle
from hope.domain.execution.models import Environment, OrderSide
from hope.domain.execution.session import ExecutionSession
from hope.domain.execution.simulator import CostModel, ExecutionQuote, Fill, simulate_market_fill
from hope.domain.execution.timeline import ExecutionTimeline
from hope.domain.portfolio.ledger import PortfolioLedger, PortfolioState
from hope.domain.risk.models import RiskAssessment
from hope.domain.signal.models import Signal
from hope.domain.trading.kernel import OrderIntent, create_order_intent, materialize_order


@dataclass(frozen=True)
class TradingKernelResult:
    intent: OrderIntent | None
    order_id: UUID | None
    fill: Fill | None
    portfolio_state: PortfolioState
    audit_events: tuple[AuditEvent, ...]


class TradingKernel:
    """Deterministic signal -> risk -> order -> timed fill -> ledger orchestration."""

    def __init__(self, ledger: PortfolioLedger) -> None:
        self._ledger = ledger
        self._execution_sessions: dict[UUID, ExecutionSession] = {}

    @staticmethod
    def _hash_payload(*parts: object) -> str:
        canonical = "|".join(str(part) for part in parts)
        return sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def _audit_event(
        cls,
        *,
        event_type: AuditEventType,
        event_time: datetime,
        environment: Environment,
        payload_hash: str,
        signal_id: UUID | None = None,
        order_id: UUID | None = None,
        fill_id: UUID | None = None,
        instrument_id: UUID | None = None,
    ) -> AuditEvent:
        canonical_time = event_time.astimezone(timezone.utc).isoformat()
        canonical = "|".join(
            (
                event_type.value,
                canonical_time,
                environment.value,
                str(signal_id or ""),
                str(order_id or ""),
                str(fill_id or ""),
                str(instrument_id or ""),
                payload_hash,
            )
        )
        return AuditEvent(
            event_id=uuid5(NAMESPACE_URL, f"hope:audit:{canonical}"),
            event_type=event_type,
            event_time=event_time,
            signal_id=signal_id,
            order_id=order_id,
            fill_id=fill_id,
            instrument_id=instrument_id,
            environment=environment.value,
            payload_hash=payload_hash,
        )

    def _execution_session(
        self,
        intent: OrderIntent,
        order_id: UUID,
    ) -> ExecutionSession:
        order = materialize_order(intent, order_id)
        session = self._execution_sessions.get(order_id)
        if session is None:
            session = ExecutionSession(OrderLifecycle(order), self._ledger)
            self._execution_sessions[order_id] = session
            return session
        if session.state.lifecycle.order != order:
            raise ValueError("ORDER_ID_REUSED_WITH_DIFFERENT_INTENT")
        return session

    def process(
        self,
        signal: Signal,
        risk: RiskAssessment,
        side: OrderSide,
        environment: Environment,
        quote: ExecutionQuote | None,
        cost_model: CostModel | None,
        *,
        order_id: UUID | None = None,
        fill_id: UUID | None = None,
        timeline: ExecutionTimeline | None = None,
    ) -> TradingKernelResult:
        events: list[AuditEvent] = []
        now = signal.decision_time
        signal_payload_hash = self._hash_payload(
            signal.signal_id, signal.instrument_id, signal.inputs_hash
        )
        events.append(
            self._audit_event(
                event_type=AuditEventType.SIGNAL_ACCEPTED,
                event_time=signal.decision_time,
                signal_id=signal.signal_id,
                instrument_id=signal.instrument_id,
                environment=environment,
                payload_hash=signal_payload_hash,
            )
        )

        intent = create_order_intent(signal, risk, side, environment)
        if intent is None:
            risk_payload_hash = self._hash_payload(
                signal.signal_id, risk.decision, risk.reason_code
            )
            events.append(
                self._audit_event(
                    event_type=AuditEventType.RISK_REJECTED,
                    event_time=now,
                    signal_id=signal.signal_id,
                    instrument_id=signal.instrument_id,
                    environment=environment,
                    payload_hash=risk_payload_hash,
                )
            )
            validate_audit_sequence(events)
            return TradingKernelResult(None, None, None, self._ledger.state, tuple(events))

        if environment is Environment.PAPER and (quote is None or cost_model is None):
            raise ValueError("PAPER_EXECUTION_REQUIRES_QUOTE_AND_COST_MODEL")
        if quote is not None and quote.event_time < signal.decision_time:
            raise ValueError("QUOTE_PRECEDES_SIGNAL_DECISION_TIME")
        if quote is not None and timeline is None:
            raise ValueError("QUOTE_EXECUTION_REQUIRES_TIMELINE")
        if timeline is not None:
            if timeline.decision_time != signal.decision_time:
                raise ValueError("TIMELINE_SIGNAL_DECISION_MISMATCH")
            if timeline.order_time < signal.decision_time:
                raise ValueError("TIMELINE_ORDER_PRECEDES_SIGNAL")

        risk_payload_hash = self._hash_payload(
            signal.signal_id, risk.approved_quantity, risk.reason_code
        )
        events.append(
            self._audit_event(
                event_type=AuditEventType.RISK_APPROVED,
                event_time=now,
                signal_id=signal.signal_id,
                instrument_id=signal.instrument_id,
                environment=environment,
                payload_hash=risk_payload_hash,
            )
        )

        actual_order_id = order_id or uuid4()
        self._execution_session(intent, actual_order_id)
        order_event_time = timeline.order_time if timeline is not None else now
        order_payload_hash = self._hash_payload(
            actual_order_id, intent.quantity, intent.side
        )
        events.append(
            self._audit_event(
                event_type=AuditEventType.ORDER_CREATED,
                event_time=order_event_time,
                signal_id=intent.signal_id,
                order_id=actual_order_id,
                instrument_id=intent.instrument_id,
                environment=intent.environment,
                payload_hash=order_payload_hash,
            )
        )

        if quote is None or cost_model is None:
            validate_audit_sequence(events)
            return TradingKernelResult(intent, actual_order_id, None, self._ledger.state, tuple(events))
        execution = self.execute_order(
            intent,
            actual_order_id,
            quote,
            cost_model,
            decision_time=signal.decision_time,
            fill_id=fill_id,
            timeline=timeline,
        )
        combined = tuple(events) + execution.audit_events
        validate_audit_sequence(combined)
        return TradingKernelResult(
            intent, actual_order_id, execution.fill, execution.portfolio_state, combined
        )

    def execute_order(
        self,
        intent: OrderIntent,
        order_id: UUID,
        quote: ExecutionQuote,
        cost_model: CostModel,
        *,
        decision_time: datetime,
        fill_id: UUID | None = None,
        timeline: ExecutionTimeline,
        quantity: Decimal | None = None,
    ) -> TradingKernelResult:
        if timeline.decision_time != decision_time:
            raise ValueError("TIMELINE_SIGNAL_DECISION_MISMATCH")
        if timeline.order_time < decision_time:
            raise ValueError("TIMELINE_ORDER_PRECEDES_SIGNAL")

        session = self._execution_session(intent, order_id)
        order = session.state.lifecycle.order
        actual_fill_id = fill_id or uuid4()
        fill = simulate_market_fill(
            order, quote, actual_fill_id, cost_model, quantity=quantity, timeline=timeline
        )
        execution_time = fill.fill_time or quote.event_time
        fill_payload_hash = self._hash_payload(
            fill.fill_id, fill.quantity, fill.price, fill.commission
        )
        events = [
            self._audit_event(
                event_type=AuditEventType.FILL_CREATED,
                event_time=execution_time,
                signal_id=fill.signal_id,
                order_id=fill.order_id,
                fill_id=fill.fill_id,
                instrument_id=fill.instrument_id,
                environment=order.environment,
                payload_hash=fill_payload_hash,
            )
        ]

        session_state = session.apply_fill(fill)
        state = session_state.portfolio
        portfolio_payload_hash = self._hash_payload(
            fill.fill_id, state.cash, state.positions[fill.instrument_id].quantity
        )
        events.append(
            self._audit_event(
                event_type=AuditEventType.PORTFOLIO_UPDATED,
                event_time=execution_time,
                signal_id=fill.signal_id,
                order_id=fill.order_id,
                fill_id=fill.fill_id,
                instrument_id=fill.instrument_id,
                environment=order.environment,
                payload_hash=portfolio_payload_hash,
            )
        )
        return TradingKernelResult(intent, order_id, fill, state, tuple(events))
