from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid4, uuid5

from hope.application.paper.effects import PaperEffect, PaperEffectType
from hope.application.paper.fills import paper_fill_payload_hash
from hope.application.paper.orders import paper_order_payload_hash
from hope.application.paper.pnl import PaperPnLEvent, paper_pnl_payload_hash
from hope.application.paper.portfolio_pnl import (
    PaperPortfolioPnLEvent,
    paper_portfolio_pnl_event_id,
    paper_portfolio_pnl_payload_hash,
)
from hope.application.paper.risk import paper_risk_payload_hash
from hope.application.paper.signals import paper_signal_payload_hash
from hope.application.paper.terminals import paper_terminal_payload_hash
from hope.domain.execution.models import (
    Environment,
    ExecutionCancellation,
    ExecutionRejection,
    Order,
    OrderSide,
)
from hope.domain.execution.simulator import Fill
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.scheduling.paper_lineage import verify_paper_recovery_lineage


def _effect(job_run_id, effect_type, entity_id, payload_hash="0" * 64):
    return PaperEffect(
        effect_id=uuid5(NAMESPACE_URL, f"hope:paper:effect:{effect_type.value}:{entity_id}"),
        job_run_id=job_run_id,
        effect_type=effect_type,
        entity_id=entity_id,
        payload_hash=payload_hash,
    )


def _approved_risk(signal_id):
    return RiskAssessment(
        signal_id=signal_id,
        decision=RiskDecision.APPROVE,
        reason_code="TEST_APPROVED",
        approved_quantity=Decimal("10"),
    )


def _risk_row(assessment):
    return {
        "decision": assessment.decision.value,
        "reason_code": assessment.reason_code,
        "approved_quantity": assessment.approved_quantity,
    }


def _signal(signal_id, instrument_id=None):
    return Signal(
        signal_id=signal_id,
        instrument_id=instrument_id or uuid4(),
        strategy_version="paper-recovery-v1",
        decision_time=datetime(2026, 10, 2, 14, 59, tzinfo=UTC),
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.75"),
        inputs_hash="a" * 64,
    )


def _signal_row(signal):
    return {
        "instrument_id": signal.instrument_id,
        "decision_time": signal.decision_time,
        "state": "SIGNAL",
        "strategy_version": signal.strategy_version,
        "signal_type": signal.signal_type.value,
        "conviction": signal.conviction,
        "inputs_hash": signal.inputs_hash,
    }


def _order(order_id, signal):
    return Order(
        order_id=order_id,
        signal_id=signal.signal_id,
        instrument_id=signal.instrument_id,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        environment=Environment.PAPER,
        signal_type=signal.signal_type,
    )


def _order_row(order):
    return {
        "signal_id": order.signal_id,
        "instrument_id": order.instrument_id,
        "environment": order.environment.value,
        "side": order.side.value,
        "quantity": order.quantity,
        "signal_type": order.signal_type.value,
    }


class _MappingsResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def one_or_none(self):
        return self._row


class _LineageConnection:
    def __init__(self, rows):
        self._rows = iter(rows)

    def execute(self, statement):
        return _MappingsResult(next(self._rows))


def _filled_fixture():
    job_run_id = uuid4()
    signal_id = uuid4()
    order_id = uuid4()
    fill_id = uuid4()
    pnl_event_id = uuid4()
    signal = _signal(signal_id)
    order = _order(order_id, signal)
    risk = _approved_risk(signal_id)
    fill = Fill(
        fill_id=fill_id,
        order_id=order_id,
        signal_id=signal_id,
        instrument_id=signal.instrument_id,
        side=order.side,
        quantity=order.quantity,
        price=Decimal("100.25"),
        commission=Decimal("0.50"),
        slippage=Decimal("0.10"),
        cost_model_version="paper-recovery-cost-v1",
        fill_time=datetime(2026, 10, 2, 15, 0, tzinfo=UTC),
    )
    pnl_event = PaperPnLEvent(
        pnl_event_id=pnl_event_id,
        position_id=uuid4(),
        amount=Decimal("12.50"),
        event_time=datetime(2026, 10, 2, 15, 1, tzinfo=UTC),
    )
    effects = (
        _effect(job_run_id, PaperEffectType.SIGNAL, signal_id, paper_signal_payload_hash(signal)),
        _effect(job_run_id, PaperEffectType.RISK, signal_id, paper_risk_payload_hash(risk)),
        _effect(job_run_id, PaperEffectType.ORDER, order_id, paper_order_payload_hash(order)),
        _effect(job_run_id, PaperEffectType.FILL, fill_id, paper_fill_payload_hash(fill)),
        _effect(job_run_id, PaperEffectType.PNL, pnl_event_id, paper_pnl_payload_hash(pnl_event)),
    )
    return effects, signal, order, risk, fill, pnl_event


def _fill_row(fill):
    return {
        "order_id": fill.order_id,
        "quantity": fill.quantity,
        "fill_price": fill.price,
        "slippage": fill.slippage,
        "transaction_cost": fill.commission,
        "filled_at": fill.fill_time,
        "cost_model_version": fill.cost_model_version,
    }


def _pnl_row(event):
    return {
        "position_id": event.position_id,
        "amount": event.amount,
        "event_time": event.event_time,
    }


def test_verifier_accepts_one_coherent_filled_execution_lineage() -> None:
    effects, signal, order, risk, fill, pnl_event = _filled_fixture()
    connection = _LineageConnection(
        (
            _signal_row(signal),
            _risk_row(risk),
            _order_row(order),
            _fill_row(fill),
            _pnl_row(pnl_event),
            {
                "instrument_id": signal.instrument_id,
                "opened_from_signal_id": signal.signal_id,
                "opened_at": signal.decision_time,
            },
        )
    )

    assert verify_paper_recovery_lineage(connection, effects) is True


def test_verifier_accepts_authoritative_portfolio_pnl_lineage() -> None:
    effects, signal, order, risk, fill, _ = _filled_fixture()
    portfolio_id = uuid4()
    pnl_event = PaperPortfolioPnLEvent(
        pnl_event_id=paper_portfolio_pnl_event_id(portfolio_id, fill.fill_id),
        portfolio_id=portfolio_id,
        fill_id=fill.fill_id,
        instrument_id=fill.instrument_id,
        realized_pnl_delta=Decimal("0"),
        commission_delta=fill.commission,
        event_time=fill.fill_time,
    )
    effects = effects[:-1] + (
        _effect(
            effects[-1].job_run_id,
            PaperEffectType.PNL,
            pnl_event.pnl_event_id,
            paper_portfolio_pnl_payload_hash(pnl_event),
        ),
    )
    connection = _LineageConnection(
        (
            _signal_row(signal),
            _risk_row(risk),
            _order_row(order),
            _fill_row(fill),
            None,
            {
                "portfolio_id": pnl_event.portfolio_id,
                "fill_id": pnl_event.fill_id,
                "instrument_id": pnl_event.instrument_id,
                "realized_pnl_delta": pnl_event.realized_pnl_delta,
                "commission_delta": pnl_event.commission_delta,
                "event_time": pnl_event.event_time,
            },
        )
    )

    assert verify_paper_recovery_lineage(connection, effects) is True


def test_verifier_rejects_portfolio_pnl_linked_to_different_fill() -> None:
    effects, signal, order, risk, fill, _ = _filled_fixture()
    portfolio_id = uuid4()
    pnl_event = PaperPortfolioPnLEvent(
        pnl_event_id=paper_portfolio_pnl_event_id(portfolio_id, fill.fill_id),
        portfolio_id=portfolio_id,
        fill_id=fill.fill_id,
        instrument_id=fill.instrument_id,
        realized_pnl_delta=Decimal("0"),
        commission_delta=fill.commission,
        event_time=fill.fill_time,
    )
    effects = effects[:-1] + (
        _effect(
            effects[-1].job_run_id,
            PaperEffectType.PNL,
            pnl_event.pnl_event_id,
            paper_portfolio_pnl_payload_hash(pnl_event),
        ),
    )
    connection = _LineageConnection(
        (
            _signal_row(signal),
            _risk_row(risk),
            _order_row(order),
            _fill_row(fill),
            None,
            {
                "portfolio_id": pnl_event.portfolio_id,
                "fill_id": uuid4(),
                "instrument_id": pnl_event.instrument_id,
                "realized_pnl_delta": pnl_event.realized_pnl_delta,
                "commission_delta": pnl_event.commission_delta,
                "event_time": pnl_event.event_time,
            },
        )
    )

    assert verify_paper_recovery_lineage(connection, effects) is False


def test_verifier_rejects_portfolio_pnl_commission_mismatch() -> None:
    effects, signal, order, risk, fill, _ = _filled_fixture()
    portfolio_id = uuid4()
    pnl_event = PaperPortfolioPnLEvent(
        pnl_event_id=paper_portfolio_pnl_event_id(portfolio_id, fill.fill_id),
        portfolio_id=portfolio_id,
        fill_id=fill.fill_id,
        instrument_id=fill.instrument_id,
        realized_pnl_delta=Decimal("0"),
        commission_delta=fill.commission + Decimal("0.01"),
        event_time=fill.fill_time,
    )
    effects = effects[:-1] + (
        _effect(
            effects[-1].job_run_id,
            PaperEffectType.PNL,
            pnl_event.pnl_event_id,
            paper_portfolio_pnl_payload_hash(pnl_event),
        ),
    )
    connection = _LineageConnection(
        (
            _signal_row(signal),
            _risk_row(risk),
            _order_row(order),
            _fill_row(fill),
            None,
            {
                "portfolio_id": pnl_event.portfolio_id,
                "fill_id": pnl_event.fill_id,
                "instrument_id": pnl_event.instrument_id,
                "realized_pnl_delta": pnl_event.realized_pnl_delta,
                "commission_delta": pnl_event.commission_delta,
                "event_time": pnl_event.event_time,
            },
        )
    )

    assert verify_paper_recovery_lineage(connection, effects) is False


def test_verifier_rejects_missing_canonical_signal_recovery_material() -> None:
    effects, signal, _, _, _, _ = _filled_fixture()
    row = _signal_row(signal)
    row["strategy_version"] = None

    assert verify_paper_recovery_lineage(_LineageConnection((row,)), effects) is False


def test_verifier_rejects_signal_payload_mismatch() -> None:
    effects, signal, _, _, _, _ = _filled_fixture()
    corrupted = (
        _effect(effects[0].job_run_id, PaperEffectType.SIGNAL, signal.signal_id, "f" * 64),
    ) + effects[1:]

    assert verify_paper_recovery_lineage(
        _LineageConnection((_signal_row(signal),)), corrupted
    ) is False


def test_verifier_rejects_order_payload_mismatch() -> None:
    effects, signal, order, risk, _, _ = _filled_fixture()
    corrupted = effects[:2] + (
        _effect(effects[2].job_run_id, PaperEffectType.ORDER, order.order_id, "f" * 64),
    ) + effects[3:]

    assert verify_paper_recovery_lineage(
        _LineageConnection((_signal_row(signal), _risk_row(risk), _order_row(order))),
        corrupted,
    ) is False


def test_verifier_rejects_order_signal_type_mismatch() -> None:
    effects, signal, order, risk, _, _ = _filled_fixture()
    row = _order_row(order)
    row["signal_type"] = SignalType.EXIT.value

    assert verify_paper_recovery_lineage(
        _LineageConnection((_signal_row(signal), _risk_row(risk), row)), effects
    ) is False


def test_verifier_rejects_order_quantity_above_durable_risk_approval() -> None:
    effects, signal, order, _, _, _ = _filled_fixture()
    risk = RiskAssessment(
        signal_id=signal.signal_id,
        decision=RiskDecision.APPROVE,
        reason_code="TEST_APPROVED",
        approved_quantity=order.quantity - Decimal("1"),
    )
    effects = effects[:1] + (
        _effect(
            effects[1].job_run_id,
            PaperEffectType.RISK,
            signal.signal_id,
            paper_risk_payload_hash(risk),
        ),
    ) + effects[2:]

    assert verify_paper_recovery_lineage(
        _LineageConnection(
            (_signal_row(signal), _risk_row(risk), _order_row(order))
        ),
        effects,
    ) is False


def test_verifier_rejects_fill_linked_to_different_order() -> None:
    effects, signal, order, risk, fill, _ = _filled_fixture()
    mismatched_fill = _fill_row(fill)
    mismatched_fill["order_id"] = uuid4()
    connection = _LineageConnection(
        (
            _signal_row(signal),
            _risk_row(risk),
            _order_row(order),
            mismatched_fill,
        )
    )

    assert verify_paper_recovery_lineage(connection, effects) is False


def test_verifier_rejects_fill_payload_mismatch() -> None:
    effects, signal, order, risk, fill, _ = _filled_fixture()
    corrupted = effects[:3] + (
        _effect(
            effects[3].job_run_id,
            PaperEffectType.FILL,
            fill.fill_id,
            "f" * 64,
        ),
    ) + effects[4:]

    assert verify_paper_recovery_lineage(
        _LineageConnection(
            (
                _signal_row(signal),
                _risk_row(risk),
                _order_row(order),
                _fill_row(fill),
            )
        ),
        corrupted,
    ) is False


def test_verifier_rejects_fill_that_predates_signal_decision() -> None:
    effects, signal, order, risk, fill, _ = _filled_fixture()
    premature_fill = Fill(
        fill_id=fill.fill_id,
        order_id=fill.order_id,
        signal_id=fill.signal_id,
        instrument_id=fill.instrument_id,
        side=fill.side,
        quantity=fill.quantity,
        price=fill.price,
        commission=fill.commission,
        slippage=fill.slippage,
        cost_model_version=fill.cost_model_version,
        fill_time=signal.decision_time - timedelta(minutes=1),
    )
    effects = effects[:3] + (
        _effect(
            effects[3].job_run_id,
            PaperEffectType.FILL,
            premature_fill.fill_id,
            paper_fill_payload_hash(premature_fill),
        ),
    ) + effects[4:]

    assert verify_paper_recovery_lineage(
        _LineageConnection(
            (
                _signal_row(signal),
                _risk_row(risk),
                _order_row(order),
                _fill_row(premature_fill),
            )
        ),
        effects,
    ) is False


def test_verifier_rejects_partial_fill_as_complete_execution() -> None:
    effects, signal, order, risk, fill, _ = _filled_fixture()
    partial_fill = Fill(
        fill_id=fill.fill_id,
        order_id=fill.order_id,
        signal_id=fill.signal_id,
        instrument_id=fill.instrument_id,
        side=fill.side,
        quantity=fill.quantity - Decimal("1"),
        price=fill.price,
        commission=fill.commission,
        slippage=fill.slippage,
        cost_model_version=fill.cost_model_version,
        fill_time=fill.fill_time,
    )
    effects = effects[:3] + (
        _effect(
            effects[3].job_run_id,
            PaperEffectType.FILL,
            partial_fill.fill_id,
            paper_fill_payload_hash(partial_fill),
        ),
    ) + effects[4:]

    assert verify_paper_recovery_lineage(
        _LineageConnection(
            (
                _signal_row(signal),
                _risk_row(risk),
                _order_row(order),
                _fill_row(partial_fill),
            )
        ),
        effects,
    ) is False


def test_verifier_rejects_pnl_payload_mismatch() -> None:
    effects, signal, order, risk, fill, pnl_event = _filled_fixture()
    corrupted = effects[:-1] + (
        _effect(
            effects[-1].job_run_id,
            PaperEffectType.PNL,
            pnl_event.pnl_event_id,
            "f" * 64,
        ),
    )

    assert verify_paper_recovery_lineage(
        _LineageConnection(
            (
                _signal_row(signal),
                _risk_row(risk),
                _order_row(order),
                _fill_row(fill),
                _pnl_row(pnl_event),
            )
        ),
        corrupted,
    ) is False


def test_verifier_rejects_pnl_that_predates_fill() -> None:
    effects, signal, order, risk, fill, pnl_event = _filled_fixture()
    premature_pnl = PaperPnLEvent(
        pnl_event_id=pnl_event.pnl_event_id,
        position_id=pnl_event.position_id,
        amount=pnl_event.amount,
        event_time=fill.fill_time - timedelta(minutes=1),
    )
    effects = effects[:-1] + (
        _effect(
            effects[-1].job_run_id,
            PaperEffectType.PNL,
            premature_pnl.pnl_event_id,
            paper_pnl_payload_hash(premature_pnl),
        ),
    )

    assert verify_paper_recovery_lineage(
        _LineageConnection(
            (
                _signal_row(signal),
                _risk_row(risk),
                _order_row(order),
                _fill_row(fill),
                _pnl_row(premature_pnl),
            )
        ),
        effects,
    ) is False


def test_verifier_rejects_pnl_position_from_different_signal() -> None:
    effects, signal, order, risk, fill, pnl_event = _filled_fixture()
    connection = _LineageConnection(
        (
            _signal_row(signal),
            _risk_row(risk),
            _order_row(order),
            _fill_row(fill),
            _pnl_row(pnl_event),
            {
                "instrument_id": signal.instrument_id,
                "opened_from_signal_id": uuid4(),
                "opened_at": signal.decision_time,
            },
        )
    )

    assert verify_paper_recovery_lineage(connection, effects) is False


def test_verifier_rejects_pnl_position_for_different_instrument() -> None:
    effects, signal, order, risk, fill, pnl_event = _filled_fixture()
    connection = _LineageConnection(
        (
            _signal_row(signal),
            _risk_row(risk),
            _order_row(order),
            _fill_row(fill),
            _pnl_row(pnl_event),
            {
                "instrument_id": uuid4(),
                "opened_from_signal_id": signal.signal_id,
                "opened_at": signal.decision_time,
            },
        )
    )

    assert verify_paper_recovery_lineage(connection, effects) is False


def test_verifier_rejects_pnl_position_opened_after_fill() -> None:
    effects, signal, order, risk, fill, pnl_event = _filled_fixture()
    connection = _LineageConnection(
        (
            _signal_row(signal),
            _risk_row(risk),
            _order_row(order),
            _fill_row(fill),
            _pnl_row(pnl_event),
            {
                "instrument_id": signal.instrument_id,
                "opened_from_signal_id": signal.signal_id,
                "opened_at": fill.fill_time + timedelta(seconds=1),
            },
        )
    )

    assert verify_paper_recovery_lineage(connection, effects) is False


def _non_fill_fixture(terminal_type):
    job_run_id = uuid4()
    signal_id = uuid4()
    order_id = uuid4()
    event_time = datetime(2026, 10, 2, 15, 0, tzinfo=UTC)
    signal = _signal(signal_id)
    order = _order(order_id, signal)
    risk = _approved_risk(signal_id)
    if terminal_type is PaperEffectType.REJECTION:
        outcome = ExecutionRejection(
            order_id,
            signal_id,
            signal.instrument_id,
            Environment.PAPER,
            "TEST_REJECTION",
            event_time,
        )
    else:
        outcome = ExecutionCancellation(
            order_id,
            signal_id,
            signal.instrument_id,
            Environment.PAPER,
            "TEST_CANCELLATION",
            event_time,
            Decimal("10"),
        )
    effects = (
        _effect(job_run_id, PaperEffectType.SIGNAL, signal_id, paper_signal_payload_hash(signal)),
        _effect(job_run_id, PaperEffectType.RISK, signal_id, paper_risk_payload_hash(risk)),
        _effect(job_run_id, PaperEffectType.ORDER, order_id, paper_order_payload_hash(order)),
        _effect(job_run_id, terminal_type, order_id, paper_terminal_payload_hash(outcome)),
    )
    return effects, signal, order, outcome, risk


def _terminal_rows(signal, order, outcome, risk):
    cancelled_quantity = (
        outcome.cancelled_quantity if isinstance(outcome, ExecutionCancellation) else None
    )
    return (
        _signal_row(signal),
        _risk_row(risk),
        _order_row(order),
        {
            "outcome": (
                "CANCELLED" if isinstance(outcome, ExecutionCancellation) else "REJECTED"
            ),
            "reason_code": outcome.reason_code,
            "event_time": (
                outcome.cancellation_time
                if isinstance(outcome, ExecutionCancellation)
                else outcome.rejection_time
            ),
            "cancelled_quantity": cancelled_quantity,
        },
    )


def test_verifier_accepts_coherent_rejection_lineage() -> None:
    effects, signal, order, outcome, risk = _non_fill_fixture(PaperEffectType.REJECTION)
    assert verify_paper_recovery_lineage(
        _LineageConnection(_terminal_rows(signal, order, outcome, risk)), effects
    ) is True


def test_verifier_accepts_coherent_cancellation_lineage() -> None:
    effects, signal, order, outcome, risk = _non_fill_fixture(PaperEffectType.CANCELLATION)
    assert verify_paper_recovery_lineage(
        _LineageConnection(_terminal_rows(signal, order, outcome, risk)), effects
    ) is True


def test_verifier_rejects_terminal_payload_mismatch() -> None:
    effects, signal, order, outcome, risk = _non_fill_fixture(PaperEffectType.REJECTION)
    corrupted = effects[:-1] + (
        _effect(
            effects[-1].job_run_id,
            PaperEffectType.REJECTION,
            outcome.order_id,
            "f" * 64,
        ),
    )
    assert verify_paper_recovery_lineage(
        _LineageConnection(_terminal_rows(signal, order, outcome, risk)), corrupted
    ) is False


def test_verifier_rejects_terminal_event_that_predates_signal_decision() -> None:
    effects, signal, order, outcome, risk = _non_fill_fixture(
        PaperEffectType.REJECTION
    )
    premature = ExecutionRejection(
        outcome.order_id,
        outcome.signal_id,
        outcome.instrument_id,
        outcome.environment,
        outcome.reason_code,
        signal.decision_time - timedelta(minutes=1),
    )
    effects = effects[:-1] + (
        _effect(
            effects[-1].job_run_id,
            PaperEffectType.REJECTION,
            premature.order_id,
            paper_terminal_payload_hash(premature),
        ),
    )

    assert verify_paper_recovery_lineage(
        _LineageConnection(_terminal_rows(signal, order, premature, risk)),
        effects,
    ) is False


def test_verifier_rejects_cancellation_that_leaves_unfilled_quantity_open() -> None:
    effects, signal, order, outcome, risk = _non_fill_fixture(
        PaperEffectType.CANCELLATION
    )
    incomplete = ExecutionCancellation(
        outcome.order_id,
        outcome.signal_id,
        outcome.instrument_id,
        outcome.environment,
        outcome.reason_code,
        outcome.cancellation_time,
        outcome.cancelled_quantity - Decimal("1"),
    )
    effects = effects[:-1] + (
        _effect(
            effects[-1].job_run_id,
            PaperEffectType.CANCELLATION,
            incomplete.order_id,
            paper_terminal_payload_hash(incomplete),
        ),
    )

    assert verify_paper_recovery_lineage(
        _LineageConnection(_terminal_rows(signal, order, incomplete, risk)),
        effects,
    ) is False


def test_verifier_rejects_risk_effect_without_matching_assessment_payload() -> None:
    effects, signal, _, risk, _, _ = _filled_fixture()
    corrupted = effects[:1] + (
        _effect(effects[1].job_run_id, PaperEffectType.RISK, signal.signal_id, "f" * 64),
    ) + effects[2:]
    assert verify_paper_recovery_lineage(
        _LineageConnection((_signal_row(signal), _risk_row(risk))), corrupted
    ) is False


def test_verifier_rejects_missing_durable_risk_assessment() -> None:
    effects, signal, _, _, _, _ = _filled_fixture()
    assert verify_paper_recovery_lineage(
        _LineageConnection((_signal_row(signal), None)), effects
    ) is False


def test_verifier_rejects_non_approved_durable_risk_assessment() -> None:
    effects, signal, _, _, _, _ = _filled_fixture()
    rejected = RiskAssessment(
        signal_id=signal.signal_id,
        decision=RiskDecision.REJECT,
        reason_code="TEST_REJECTED",
        approved_quantity=Decimal("0"),
    )
    effects = effects[:1] + (
        _effect(
            effects[1].job_run_id,
            PaperEffectType.RISK,
            signal.signal_id,
            paper_risk_payload_hash(rejected),
        ),
    ) + effects[2:]
    assert verify_paper_recovery_lineage(
        _LineageConnection((_signal_row(signal), _risk_row(rejected))), effects
    ) is False
