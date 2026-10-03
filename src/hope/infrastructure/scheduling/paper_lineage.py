from __future__ import annotations

from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Column,
    Connection,
    DateTime,
    MetaData,
    Numeric,
    String,
    Table,
    Uuid,
    func,
    select,
)

from hope.application.paper.effects import PaperEffect, PaperEffectType
from hope.application.paper.fills import paper_fill_payload_hash
from hope.application.paper.orders import paper_order_payload_hash
from hope.application.paper.pnl import PaperPnLEvent, paper_pnl_payload_hash
from hope.application.paper.portfolio_pnl import (
    PaperPortfolioPnLEvent,
    paper_portfolio_pnl_payload_hash,
)
from hope.application.paper.risk import paper_risk_payload_hash
from hope.application.paper.signals import paper_signal_payload_hash
from hope.application.paper.terminals import paper_terminal_payload_hash
from hope.domain.execution.models import Environment, ExecutionCancellation, ExecutionRejection, Order
from hope.domain.execution.simulator import Fill
from hope.domain.risk.models import RiskAssessment
from hope.domain.signal.models import Signal


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
    signals = Table(
        "signals",
        metadata,
        Column("signal_id", Uuid, primary_key=True),
        Column("instrument_id", Uuid, nullable=False),
        Column("decision_time", DateTime(timezone=True), nullable=False),
        Column("state", String, nullable=False),
        Column("strategy_version", String),
        Column("signal_type", String),
        Column("conviction", Numeric),
        Column("inputs_hash", String),
    )
    orders = Table(
        "orders",
        metadata,
        Column("order_id", Uuid, primary_key=True),
        Column("signal_id", Uuid, nullable=False),
        Column("instrument_id", Uuid, nullable=False),
        Column("environment", String, nullable=False),
        Column("side", String, nullable=False),
        Column("quantity", Numeric, nullable=False),
        Column("signal_type", String),
    )
    fills = Table(
        "fills",
        metadata,
        Column("fill_id", Uuid, primary_key=True),
        Column("order_id", Uuid, nullable=False),
        Column("quantity", Numeric, nullable=False),
        Column("fill_price", Numeric, nullable=False),
        Column("slippage", Numeric, nullable=False),
        Column("transaction_cost", Numeric, nullable=False),
        Column("filled_at", DateTime(timezone=True), nullable=False),
        Column("cost_model_version", String, nullable=False),
    )
    pnl_events = Table(
        "pnl_events",
        metadata,
        Column("pnl_event_id", Uuid, primary_key=True),
        Column("position_id", Uuid, nullable=False),
        Column("amount", Numeric, nullable=False),
        Column("event_time", DateTime(timezone=True), nullable=False),
    )
    portfolio_pnl_events = Table(
        "paper_portfolio_pnl_events",
        metadata,
        Column("pnl_event_id", Uuid, primary_key=True),
        Column("portfolio_id", Uuid, nullable=False),
        Column("fill_id", Uuid, nullable=False),
        Column("instrument_id", Uuid, nullable=False),
        Column("realized_pnl_delta", Numeric, nullable=False),
        Column("commission_delta", Numeric, nullable=False),
        Column("event_time", DateTime(timezone=True), nullable=False),
    )
    portfolio_fill_applications = Table(
        "paper_portfolio_fill_applications",
        metadata,
        Column("portfolio_id", Uuid, primary_key=True),
        Column("fill_id", Uuid, primary_key=True),
        Column("application_sequence", BigInteger, nullable=False),
        Column("applied_at", DateTime(timezone=True), nullable=False),
    )
    portfolios = Table(
        "paper_portfolios",
        metadata,
        Column("portfolio_id", Uuid, primary_key=True),
        Column("version", BigInteger, nullable=False),
    )
    positions = Table(
        "positions",
        metadata,
        Column("position_id", Uuid, primary_key=True),
        Column("instrument_id", Uuid, nullable=False),
        Column("opened_from_signal_id", Uuid, nullable=False),
        Column("opened_at", DateTime(timezone=True), nullable=False),
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
    risk_assessments = Table(
        "paper_risk_assessments",
        metadata,
        Column("signal_id", Uuid, primary_key=True),
        Column("decision", String, nullable=False),
        Column("reason_code", String, nullable=False),
        Column("approved_quantity", Numeric, nullable=False),
    )

    signal_row = connection.execute(
        select(
            signals.c.instrument_id,
            signals.c.decision_time,
            signals.c.state,
            signals.c.strategy_version,
            signals.c.signal_type,
            signals.c.conviction,
            signals.c.inputs_hash,
        ).where(signals.c.signal_id == signal_id)
    ).mappings().one_or_none()
    if signal_row is None or signal_row["state"] != "SIGNAL":
        return False
    try:
        signal = Signal(
            signal_id=signal_id,
            instrument_id=signal_row["instrument_id"],
            strategy_version=signal_row["strategy_version"],
            decision_time=signal_row["decision_time"],
            signal_type=signal_row["signal_type"],
            conviction=signal_row["conviction"],
            inputs_hash=signal_row["inputs_hash"],
        )
    except (TypeError, ValueError):
        return False
    if (
        by_type[PaperEffectType.SIGNAL][0].payload_hash
        != paper_signal_payload_hash(signal)
    ):
        return False

    risk_row = connection.execute(
        select(
            risk_assessments.c.decision,
            risk_assessments.c.reason_code,
            risk_assessments.c.approved_quantity,
        ).where(risk_assessments.c.signal_id == signal_id)
    ).mappings().one_or_none()
    if risk_row is None:
        return False
    try:
        risk_assessment = RiskAssessment(
            signal_id=signal_id,
            decision=risk_row["decision"],
            reason_code=risk_row["reason_code"],
            approved_quantity=risk_row["approved_quantity"],
        )
    except (TypeError, ValueError):
        return False
    if not risk_assessment.approved:
        return False
    if (
        by_type[PaperEffectType.RISK][0].payload_hash
        != paper_risk_payload_hash(risk_assessment)
    ):
        return False

    order_row = connection.execute(
        select(
            orders.c.signal_id,
            orders.c.instrument_id,
            orders.c.environment,
            orders.c.side,
            orders.c.quantity,
            orders.c.signal_type,
        ).where(orders.c.order_id == order_id)
    ).mappings().one_or_none()
    if (
        order_row is None
        or order_row["signal_id"] != signal_id
        or order_row["instrument_id"] != signal.instrument_id
        or order_row["environment"] != Environment.PAPER.value
        or order_row["signal_type"] != signal.signal_type.value
        or order_row["quantity"] != risk_assessment.approved_quantity
    ):
        return False
    try:
        order_object = Order(
            order_id=order_id,
            signal_id=signal_id,
            instrument_id=order_row["instrument_id"],
            side=order_row["side"],
            quantity=order_row["quantity"],
            environment=order_row["environment"],
            signal_type=order_row["signal_type"],
        )
    except (TypeError, ValueError):
        return False
    if (
        by_type[PaperEffectType.ORDER][0].payload_hash
        != paper_order_payload_hash(order_object)
    ):
        return False

    if terminal_type is not None:
        terminal_effect = by_type[terminal_type][0]
        if terminal_effect.entity_id != order_id:
            return False
        order = order_row
        terminal = connection.execute(
            select(
                terminal_events.c.outcome,
                terminal_events.c.reason_code,
                terminal_events.c.event_time,
                terminal_events.c.cancelled_quantity,
            ).where(terminal_events.c.order_id == order_id)
        ).mappings().one_or_none()
        if terminal is None or terminal["event_time"] < signal.decision_time:
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
                if (
                    terminal["outcome"] != "CANCELLED"
                    or terminal["cancelled_quantity"] is None
                    or terminal["cancelled_quantity"] != order_object.quantity
                ):
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
    fill = connection.execute(
        select(
            fills.c.order_id,
            fills.c.quantity,
            fills.c.fill_price,
            fills.c.slippage,
            fills.c.transaction_cost,
            fills.c.filled_at,
            fills.c.cost_model_version,
        ).where(fills.c.fill_id == fill_id)
    ).mappings().one_or_none()
    if (
        fill is None
        or fill["order_id"] != order_id
        or fill["filled_at"] < signal.decision_time
    ):
        return False
    try:
        durable_fill = Fill(
            fill_id=fill_id,
            order_id=order_id,
            signal_id=signal_id,
            instrument_id=order_object.instrument_id,
            side=order_object.side,
            quantity=fill["quantity"],
            price=fill["fill_price"],
            commission=fill["transaction_cost"],
            slippage=fill["slippage"],
            cost_model_version=fill["cost_model_version"],
            fill_time=fill["filled_at"],
        )
        expected_fill_hash = paper_fill_payload_hash(durable_fill)
    except (TypeError, ValueError):
        return False
    if durable_fill.quantity != order_object.quantity:
        return False
    if by_type[PaperEffectType.FILL][0].payload_hash != expected_fill_hash:
        return False

    pnl = connection.execute(
        select(
            pnl_events.c.position_id,
            pnl_events.c.amount,
            pnl_events.c.event_time,
        ).where(pnl_events.c.pnl_event_id == pnl_event_id)
    ).mappings().one_or_none()
    if pnl is not None:
        if pnl["event_time"] < durable_fill.fill_time:
            return False
        try:
            durable_pnl = PaperPnLEvent(
                pnl_event_id=pnl_event_id,
                position_id=pnl["position_id"],
                amount=pnl["amount"],
                event_time=pnl["event_time"],
            )
            expected_pnl_hash = paper_pnl_payload_hash(durable_pnl)
        except (TypeError, ValueError):
            return False
        if by_type[PaperEffectType.PNL][0].payload_hash != expected_pnl_hash:
            return False

        position = connection.execute(
            select(
                positions.c.instrument_id,
                positions.c.opened_from_signal_id,
                positions.c.opened_at,
            ).where(
                positions.c.position_id == pnl["position_id"]
            )
        ).mappings().one_or_none()
        return (
            position is not None
            and position["opened_from_signal_id"] == signal_id
            and position["instrument_id"] == durable_fill.instrument_id
            and position["opened_at"] <= durable_fill.fill_time
        )

    portfolio_pnl = connection.execute(
        select(
            portfolio_pnl_events.c.portfolio_id,
            portfolio_pnl_events.c.fill_id,
            portfolio_pnl_events.c.instrument_id,
            portfolio_pnl_events.c.realized_pnl_delta,
            portfolio_pnl_events.c.commission_delta,
            portfolio_pnl_events.c.event_time,
        ).where(portfolio_pnl_events.c.pnl_event_id == pnl_event_id)
    ).mappings().one_or_none()
    if portfolio_pnl is None:
        return False
    try:
        durable_portfolio_pnl = PaperPortfolioPnLEvent(
            pnl_event_id=pnl_event_id,
            portfolio_id=portfolio_pnl["portfolio_id"],
            fill_id=portfolio_pnl["fill_id"],
            instrument_id=portfolio_pnl["instrument_id"],
            realized_pnl_delta=portfolio_pnl["realized_pnl_delta"],
            commission_delta=portfolio_pnl["commission_delta"],
            event_time=portfolio_pnl["event_time"],
        )
    except (TypeError, ValueError):
        return False
    if (
        durable_portfolio_pnl.fill_id != durable_fill.fill_id
        or durable_portfolio_pnl.instrument_id != durable_fill.instrument_id
        or durable_portfolio_pnl.event_time != durable_fill.fill_time
        or durable_portfolio_pnl.commission_delta != durable_fill.commission
    ):
        return False
    counted_applications = portfolio_fill_applications.alias("counted_applications")
    application_counts = (
        select(
            counted_applications.c.portfolio_id,
            func.count().label("application_count"),
        )
        .group_by(counted_applications.c.portfolio_id)
        .subquery()
    )
    application = connection.execute(
        select(
            portfolio_fill_applications.c.application_sequence,
            portfolio_fill_applications.c.applied_at,
            portfolios.c.version,
            application_counts.c.application_count,
        )
        .select_from(
            portfolio_fill_applications.join(
                portfolios,
                portfolio_fill_applications.c.portfolio_id == portfolios.c.portfolio_id,
            ).join(
                application_counts,
                portfolio_fill_applications.c.portfolio_id
                == application_counts.c.portfolio_id,
            )
        )
        .where(
            portfolio_fill_applications.c.portfolio_id
            == durable_portfolio_pnl.portfolio_id,
            portfolio_fill_applications.c.fill_id == durable_fill.fill_id,
        )
    ).mappings().one_or_none()
    if (
        application is None
        or application["applied_at"] < durable_fill.fill_time
        or application["application_sequence"] > application["version"]
        or application["version"] != application["application_count"]
    ):
        return False
    return by_type[PaperEffectType.PNL][0].payload_hash == paper_portfolio_pnl_payload_hash(
        durable_portfolio_pnl
    )
