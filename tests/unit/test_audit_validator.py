from datetime import datetime, timezone
from uuid import uuid4
import pytest
from hope.domain.audit import AuditEvent, AuditEventType, AuditSequenceError, validate_audit_sequence


def event(kind, t, **refs):
    return AuditEvent(event_id=uuid4(), event_type=kind, event_time=t,
                      environment="PAPER", payload_hash="a" * 64, **refs)


def test_audit_sequence_requires_causal_order():
    t = datetime(2026, 8, 29, 13, 0, tzinfo=timezone.utc)
    sid, iid, oid, fid = uuid4(), uuid4(), uuid4(), uuid4()
    events = [
        event(AuditEventType.SIGNAL_ACCEPTED, t, signal_id=sid, instrument_id=iid),
        event(AuditEventType.RISK_APPROVED, t, signal_id=sid, instrument_id=iid),
        event(AuditEventType.ORDER_CREATED, t, signal_id=sid, order_id=oid, instrument_id=iid),
        event(AuditEventType.FILL_CREATED, t, signal_id=sid, order_id=oid, fill_id=fid, instrument_id=iid),
        event(AuditEventType.PORTFOLIO_UPDATED, t, signal_id=sid, order_id=oid, fill_id=fid, instrument_id=iid),
    ]
    validate_audit_sequence(events)


def test_audit_sequence_rejects_time_regression():
    t = datetime(2026, 8, 29, 13, 0, tzinfo=timezone.utc)
    sid, iid = uuid4(), uuid4()
    events = [
        event(AuditEventType.SIGNAL_ACCEPTED, t, signal_id=sid, instrument_id=iid),
        event(AuditEventType.RISK_APPROVED, t.replace(minute=1), signal_id=sid, instrument_id=iid),
        event(AuditEventType.ORDER_CREATED, t.replace(minute=0, second=30), signal_id=sid, order_id=uuid4(), instrument_id=iid),
    ]
    with pytest.raises(AuditSequenceError, match="AUDIT_EVENT_TIME_REGRESSION"):
        validate_audit_sequence(events)


def test_audit_sequence_rejects_portfolio_update_without_fill():
    t = datetime(2026, 8, 29, 13, 0, tzinfo=timezone.utc)
    sid, iid, oid, fid = uuid4(), uuid4(), uuid4(), uuid4()
    events = [
        event(AuditEventType.SIGNAL_ACCEPTED, t, signal_id=sid, instrument_id=iid),
        event(AuditEventType.RISK_APPROVED, t, signal_id=sid, instrument_id=iid),
        event(AuditEventType.ORDER_CREATED, t, signal_id=sid, order_id=oid, instrument_id=iid),
        event(AuditEventType.PORTFOLIO_UPDATED, t, signal_id=sid, order_id=oid, fill_id=fid, instrument_id=iid),
    ]
    with pytest.raises(AuditSequenceError, match="PORTFOLIO_UPDATE_BEFORE_FILL"):
        validate_audit_sequence(events)


def test_audit_sequence_requires_risk_before_order():
    t = datetime(2026, 8, 29, 13, 0, tzinfo=timezone.utc)
    sid, iid, oid = uuid4(), uuid4(), uuid4()
    events = [
        event(AuditEventType.SIGNAL_ACCEPTED, t, signal_id=sid, instrument_id=iid),
        event(AuditEventType.ORDER_CREATED, t, signal_id=sid, order_id=oid, instrument_id=iid),
    ]
    with pytest.raises(AuditSequenceError, match="ORDER_BEFORE_RISK"):
        validate_audit_sequence(events)


def test_audit_sequence_rejects_order_after_risk_rejection():
    t = datetime(2026, 8, 29, 13, 0, tzinfo=timezone.utc)
    sid, iid, oid = uuid4(), uuid4(), uuid4()
    events = [
        event(AuditEventType.SIGNAL_ACCEPTED, t, signal_id=sid, instrument_id=iid),
        event(AuditEventType.RISK_REJECTED, t, signal_id=sid, instrument_id=iid),
        event(AuditEventType.ORDER_CREATED, t, signal_id=sid, order_id=oid, instrument_id=iid),
    ]
    with pytest.raises(AuditSequenceError, match="ORDER_AFTER_RISK_REJECTION"):
        validate_audit_sequence(events)


def test_audit_sequence_rejects_cross_entity_reference_mismatch():
    t = datetime(2026, 8, 29, 13, 0, tzinfo=timezone.utc)
    sid, iid, other_iid = uuid4(), uuid4(), uuid4()
    events = [
        event(AuditEventType.SIGNAL_ACCEPTED, t, signal_id=sid, instrument_id=iid),
        event(AuditEventType.RISK_APPROVED, t, signal_id=sid, instrument_id=other_iid),
    ]
    with pytest.raises(AuditSequenceError, match="AUDIT_CROSS_ENTITY_REFERENCE_MISMATCH"):
        validate_audit_sequence(events)
