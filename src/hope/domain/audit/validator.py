from __future__ import annotations

from datetime import datetime
from .models import AuditEvent, AuditEventType


class AuditSequenceError(ValueError):
    pass


_REQUIRED_REFERENCES = {
    AuditEventType.SIGNAL_ACCEPTED: ("signal_id", "instrument_id"),
    AuditEventType.RISK_APPROVED: ("signal_id", "instrument_id"),
    AuditEventType.RISK_REJECTED: ("signal_id", "instrument_id"),
    AuditEventType.ORDER_CREATED: ("signal_id", "order_id", "instrument_id"),
    AuditEventType.EXECUTION_REJECTED: ("signal_id", "order_id", "instrument_id"),
    AuditEventType.FILL_CREATED: ("signal_id", "order_id", "fill_id", "instrument_id"),
    AuditEventType.PORTFOLIO_UPDATED: ("signal_id", "order_id", "fill_id", "instrument_id"),
}


def validate_audit_sequence(events: tuple[AuditEvent, ...] | list[AuditEvent]) -> None:
    """Validate causal ordering and required attribution of a kernel audit stream."""
    if not events:
        raise AuditSequenceError("AUDIT_SEQUENCE_EMPTY")

    previous: datetime | None = None
    seen_order = False
    seen_fill = False
    seen_risk = False
    terminal_risk_rejected = False
    terminal_execution_rejected = False
    active_signal_id = events[0].signal_id
    active_instrument_id = events[0].instrument_id
    active_order_id = None
    active_fill_ids: set = set()
    for event in events:
        required = _REQUIRED_REFERENCES[event.event_type]
        if any(getattr(event, name) is None for name in required):
            raise AuditSequenceError(f"AUDIT_MISSING_REFERENCE:{event.event_type}")
        if previous is not None and event.event_time < previous:
            raise AuditSequenceError("AUDIT_EVENT_TIME_REGRESSION")
        previous = event.event_time

        if event.signal_id != active_signal_id or event.instrument_id != active_instrument_id:
            raise AuditSequenceError("AUDIT_CROSS_ENTITY_REFERENCE_MISMATCH")
        if terminal_execution_rejected:
            raise AuditSequenceError("AUDIT_EVENT_AFTER_EXECUTION_REJECTION")

        if event.event_type is AuditEventType.SIGNAL_ACCEPTED:
            if seen_risk or seen_order or seen_fill:
                raise AuditSequenceError("SIGNAL_ACCEPTED_OUT_OF_SEQUENCE")
        elif event.event_type is AuditEventType.RISK_APPROVED:
            if seen_risk or seen_order or seen_fill:
                raise AuditSequenceError("RISK_EVENT_OUT_OF_SEQUENCE")
            seen_risk = True
        elif event.event_type is AuditEventType.RISK_REJECTED:
            if seen_risk or seen_order or seen_fill:
                raise AuditSequenceError("RISK_EVENT_OUT_OF_SEQUENCE")
            seen_risk = True
            terminal_risk_rejected = True
        elif event.event_type is AuditEventType.ORDER_CREATED:
            if not seen_risk:
                raise AuditSequenceError("ORDER_BEFORE_RISK")
            if terminal_risk_rejected:
                raise AuditSequenceError("ORDER_AFTER_RISK_REJECTION")
            if seen_order or seen_fill:
                raise AuditSequenceError("DUPLICATE_ORDER_EVENT")
            seen_order = True
            active_order_id = event.order_id
        elif event.event_type is AuditEventType.EXECUTION_REJECTED:
            if not seen_order:
                raise AuditSequenceError("EXECUTION_REJECTION_BEFORE_ORDER")
            if seen_fill:
                raise AuditSequenceError("EXECUTION_REJECTION_AFTER_FILL")
            if event.order_id != active_order_id:
                raise AuditSequenceError("EXECUTION_REJECTION_ORDER_REFERENCE_MISMATCH")
            terminal_execution_rejected = True
        elif event.event_type is AuditEventType.FILL_CREATED:
            if not seen_order:
                raise AuditSequenceError("FILL_BEFORE_ORDER")
            if event.order_id != active_order_id:
                raise AuditSequenceError("FILL_ORDER_REFERENCE_MISMATCH")
            if event.fill_id in active_fill_ids:
                raise AuditSequenceError("DUPLICATE_FILL_EVENT")
            active_fill_ids.add(event.fill_id)
            seen_fill = True
        elif event.event_type is AuditEventType.PORTFOLIO_UPDATED:
            if not seen_fill:
                raise AuditSequenceError("PORTFOLIO_UPDATE_BEFORE_FILL")
            if event.order_id != active_order_id or event.fill_id not in active_fill_ids:
                raise AuditSequenceError("PORTFOLIO_REFERENCE_MISMATCH")

    if events[0].event_type is not AuditEventType.SIGNAL_ACCEPTED:
        raise AuditSequenceError("AUDIT_MUST_START_WITH_SIGNAL")
