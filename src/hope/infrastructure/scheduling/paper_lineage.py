from __future__ import annotations

from uuid import UUID

from sqlalchemy import Column, Connection, DateTime, MetaData, Numeric, String, Table, Uuid, select

from hope.application.paper.effects import PaperEffect, PaperEffectType
from hope.application.paper.terminals import paper_terminal_payload_hash
from hope.domain.execution.models import Environment, ExecutionCancellation, ExecutionRejection


def verify_paper_recovery_lineage(
    connection: Connection,
    effects: tuple[PaperEffect, ...],
) -> bool:
    """Prove one durable PAPER execution chain without replaying it.

    Filled, rejected, and cancelled paths each require an exact effect shape. Their durable
    relational state must converge on the same signal/order lineage, and non-fill terminal
    payloads must match the immutable terminal event before recovery can acknowledge them.
    """
    by_type: dict[PaperEffectType, list[PaperEffect]] = {}
    for effect in effects:
        by_type.setdefault(effect.effect_type, []).append(effect)

    filled_required = (
        PaperEffectType.SIGNAL,
        PaperEffectType.RISK,
        PaperEffectType.ORDER,
        PaperEffectType.FILL,
        PaperEffectType.PNL,
    )
    non_fill_terminal_types = set(by_type) & {
        PaperEffectType.CANCELLATION,
        PaperEffectType.REJECTION,
    }
    if set(by_type) == set(filled_required):
        required = filled_required
        terminal_type = None
    elif len(non_fill_terminal_types) == 1:
        terminal_type = next(iter(non_fill_terminal_types))
        required = (
            PaperEffectType.SIGNAL,
            PaperEffectType.RISK,
            PaperEffectType.ORDER,
            terminal_type,
        )
        if set(by_type) != set(required):
            return False
    else:
        return False
    if any(len(by_type[effect_type]) != 1 for effect_type in required):
        return False

    signal_id = by_type[PaperEffectType.SIGNAL][0].entity_id
    risk_signal_id = by_type[PaperEffectType.RISK][0].entity_id
    order_id = by_type[PaperEffectType.ORDER][0].entity_id
    if risk_signal_id != signal_id:
        return False

    metadata = MetaData()
    orders = Table(
        "orders",
        metadata,
        Column("order_id", Uuid, primary_key=True),
        Column("signal_id", Uuid, nullable=False),
        Column("instrument_id", Uuid, nullable=False),
        Column("environment", String, nullable=False),
    )
    fills = Table(
        "fills",
        metadata,
        Column("fill_id", Uuid, primary_key=True),
        Column("order_id", Uuid, nullable=False),
    )
    pnl_events = Table(
        "pnl_events",
        metadata,
        Column("pnl_event_id", Uuid, primary_key=True),
        Column("position_id", Uuid, nullable=False),
    )
    positions = Table(
        "positions",
        metadata,
        Column("position_id", Uuid, primary_key=True),
        Column("opened_from_signal_id", Uuid, nullable=False),
    )
    terminal_events = Table(
        "paper_order_terminal_events",
        metadata,
        Column("order_id", Uuid, primary_key=True),
        Column("outcome", String, nullable=False),
        Column("reason_code", String, nullable=False),
        Column("event_time", DateTime(timezone=True), nullable=False),
        Column("cancelled_quantity", Numeric),
    )

    if terminal_type is not None:
        terminal_effect = by_type[terminal_type][0]
        if terminal_effect.entity_id != order_id:
            return False
        order = connection.execute(
            select(
                orders.c.signal_id,
                orders.c.instrument_id,
                orders.c.environment,
            ).where(orders.c.order_id == order_id)
        ).mappings().one_or_none()
        if (
            order is None
            or order["signal_id"] != signal_id
            or order["environment"] != Environment.PAPER.value
        ):
            return False
        terminal = connection.execute(
            select(
                terminal_events.c.outcome,
                terminal_events.c.reason_code,
                terminal_events.c.event_time,
                terminal_events.c.cancelled_quantity,
            ).where(terminal_events.c.order_id == order_id)
        ).mappings().one_or_none()
        if terminal is None:
            return False
        try:
            if terminal_type is PaperEffectType.REJECTION:
                if (
                    terminal["outcome"] != "REJECTED"
                    or terminal["cancelled_quantity"] is not None
                ):
                    return False
                outcome = ExecutionRejection(
                    order_id,
                    signal_id,
                    order["instrument_id"],
                    Environment.PAPER,
                    terminal["reason_code"],
                    terminal["event_time"],
                )
            else:
                if terminal["outcome"] != "CANCELLED" or terminal["cancelled_quantity"] is None:
                    return False
                outcome = ExecutionCancellation(
                    order_id,
                    signal_id,
                    order["instrument_id"],
                    Environment.PAPER,
                    terminal["reason_code"],
                    terminal["event_time"],
                    terminal["cancelled_quantity"],
                )
        except (TypeError, ValueError):
            return False
        return terminal_effect.payload_hash == paper_terminal_payload_hash(outcome)

    fill_id = by_type[PaperEffectType.FILL][0].entity_id
    pnl_event_id = by_type[PaperEffectType.PNL][0].entity_id
    order = connection.execute(
        select(orders.c.signal_id).where(orders.c.order_id == order_id)
    ).mappings().one_or_none()
    if order is None or order["signal_id"] != signal_id:
        return False

    fill = connection.execute(
        select(fills.c.order_id).where(fills.c.fill_id == fill_id)
    ).mappings().one_or_none()
    if fill is None or fill["order_id"] != order_id:
        return False

    pnl = connection.execute(
        select(pnl_events.c.position_id).where(pnl_events.c.pnl_event_id == pnl_event_id)
    ).mappings().one_or_none()
    if pnl is None:
        return False

    position = connection.execute(
        select(positions.c.opened_from_signal_id).where(
            positions.c.position_id == pnl["position_id"]
        )
    ).mappings().one_or_none()
    if position is None or position["opened_from_signal_id"] != signal_id:
        return False

    return True
