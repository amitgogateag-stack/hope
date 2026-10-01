from __future__ import annotations

from uuid import UUID

from sqlalchemy import Column, Connection, MetaData, Table, Uuid, select

from hope.application.paper.effects import PaperEffect, PaperEffectType


def verify_paper_recovery_lineage(
    connection: Connection,
    effects: tuple[PaperEffect, ...],
) -> bool:
    """Prove one durable filled PAPER execution chain without replaying it.

    This verifier is intentionally narrow. It only proves the filled path when the run has
    exactly one SIGNAL, RISK, ORDER, FILL and PNL effect and their durable relational state
    converges on the same signal/order lineage. Other terminal paths remain fail-closed until
    they receive their own explicit proof contract.
    """
    by_type: dict[PaperEffectType, list[PaperEffect]] = {}
    for effect in effects:
        by_type.setdefault(effect.effect_type, []).append(effect)

    required = (
        PaperEffectType.SIGNAL,
        PaperEffectType.RISK,
        PaperEffectType.ORDER,
        PaperEffectType.FILL,
        PaperEffectType.PNL,
    )
    if set(by_type) != set(required):
        return False
    if any(len(by_type[effect_type]) != 1 for effect_type in required):
        return False

    signal_id = by_type[PaperEffectType.SIGNAL][0].entity_id
    risk_signal_id = by_type[PaperEffectType.RISK][0].entity_id
    order_id = by_type[PaperEffectType.ORDER][0].entity_id
    fill_id = by_type[PaperEffectType.FILL][0].entity_id
    pnl_event_id = by_type[PaperEffectType.PNL][0].entity_id
    if risk_signal_id != signal_id:
        return False

    metadata = MetaData()
    orders = Table(
        "orders",
        metadata,
        Column("order_id", Uuid, primary_key=True),
        Column("signal_id", Uuid, nullable=False),
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
