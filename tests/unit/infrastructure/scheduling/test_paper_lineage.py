from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

from hope.application.paper.effects import PaperEffect, PaperEffectType
from hope.application.paper.fills import paper_fill_payload_hash
from hope.application.paper.no_trade import (
    PaperNoTradeDecision,
    paper_no_trade_payload_hash,
)
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
from hope.infrastructure.scheduling import paper_lineage
from hope.infrastructure.scheduling.paper_lineage import verify_paper_recovery_lineage


def _effect(job_run_id, effect_type, entity_id, payload_hash="0" * 64):
    return PaperEffect(
        effect_id=uuid5(NAMESPACE_URL, f"hope:paper:effect:{effect_type.value}:{entity_id}"),
        job_run_id=job_run_id,
        effect_type=effect_type,
        entity_id=entity_id,
        payload_hash=payload_hash,
    )


def test_no_trade_lineage_requires_matching_job_owned_decision() -> None:
    job_run_id = uuid4()
    decision = PaperNoTradeDecision(
        decision_id=job_run_id,
        strategy_version="strategy-v1",
        decision_time=datetime(2026, 10, 15, 13, 0, tzinfo=UTC),
        inputs_hash="a" * 64,
    )
    effect = _effect(
        job_run_id,
        PaperEffectType.NO_TRADE,
        job_run_id,
        paper_no_trade_payload_hash(decision),
    )
    row = {
        "decision_id": decision.decision_id,
        "job_run_id": job_run_id,
        "strategy_version": decision.strategy_version,
        "decision_time": decision.decision_time,
        "inputs_hash": decision.inputs_hash,
    }

    assert verify_paper_recovery_lineage(
        _LineageConnection((row,)), (effect,), require_portfolio_pnl=True
    ) is True

    assert verify_paper_recovery_lineage(
        _LineageConnection(({**row, "inputs_hash": "b" * 64},)),
        (effect,),
        require_portfolio_pnl=True,
    ) is False


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


class _FakePortfolioRepository:
    load_result = object()
    load_error = None
    loaded_portfolios = []

    def __init__(self, connection):
        self.connection = connection

    def verify_accounting_history(self, portfolio_id, *, current_job_run_id=None):
        type(self).loaded_portfolios.append((portfolio_id, current_job_run_id))
        if type(self).load_error is not None:
            raise type(self).load_error
        return type(self).load_result


@pytest.fixture(autouse=True)
def _reset_portfolio_repository(monkeypatch):
    _FakePortfolioRepository.load_result = object()
    _FakePortfolioRepository.load_error = None
    _FakePortfolioRepository.loaded_portfolios = []
    monkeypatch.setattr(
        paper_lineage,
        "SqlAlchemyPaperPortfolioRepository",
        _FakePortfolioRepository,
    )


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


def _recoverable_effect_row(effect):
    return {
        "effect_id": effect.effect_id,
        "job_run_id": effect.job_run_id,
        "effect_type": effect.effect_type.value,
        "entity_id": effect.entity_id,
        "payload_hash": effect.payload_hash,
        "owner_status": "SUCCEEDED",
    }


def test_staged_signal_batch_authenticates_every_durable_signal() -> None:
    job_run_id = uuid4()
    signals = (_signal(uuid4()), _signal(uuid4()))
    effects = tuple(
        _effect(
            job_run_id,
            PaperEffectType.SIGNAL,
            signal.signal_id,
            paper_signal_payload_hash(signal),
        )
        for signal in signals
    )

    assert verify_paper_recovery_lineage(
        _LineageConnection(tuple(_signal_row(signal) for signal in signals)),
        effects,
        require_portfolio_pnl=True,
    ) is True


@pytest.mark.parametrize("tamper", ["missing", "payload", "duplicate", "owner"])
def test_staged_signal_batch_rejects_unauthenticated_or_ambiguous_signal(
    tamper,
) -> None:
    job_run_id = uuid4()
    signals = (_signal(uuid4()), _signal(uuid4()))
    effects = [
        _effect(
            job_run_id,
            PaperEffectType.SIGNAL,
            signal.signal_id,
            paper_signal_payload_hash(signal),
        )
        for signal in signals
    ]
    rows = [_signal_row(signal) for signal in signals]
    if tamper == "missing":
        rows[1] = None
    elif tamper == "payload":
        effects[1] = _effect(
            job_run_id, PaperEffectType.SIGNAL, signals[1].signal_id, "f" * 64,
        )
    elif tamper == "duplicate":
        effects[1] = effects[0]
        rows = []
    else:
        effects[1] = _effect(
            uuid4(),
            PaperEffectType.SIGNAL,
            signals[1].signal_id,
            paper_signal_payload_hash(signals[1]),
        )
        rows = []

    assert verify_paper_recovery_lineage(
        _LineageConnection(tuple(rows)),
        tuple(effects),
        require_portfolio_pnl=True,
    ) is False


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
    assert _FakePortfolioRepository.loaded_portfolios == []


def test_reconciliation_lineage_rejects_legacy_position_pnl() -> None:
    """Legacy PNL remains valid history, not proof of portfolio reconciliation."""
    effects, signal, order, risk, fill, pnl_event = _filled_fixture()
    connection = _LineageConnection(
        (
            _signal_row(signal),
            _risk_row(risk),
            _order_row(order),
            _fill_row(fill),
            _pnl_row(pnl_event),
        )
    )

    assert verify_paper_recovery_lineage(
        connection, effects, require_portfolio_pnl=True,
    ) is False
    assert _FakePortfolioRepository.loaded_portfolios == []


@pytest.mark.parametrize("require_portfolio_pnl", [False, True])
def test_verifier_accepts_authoritative_portfolio_pnl_lineage(
    require_portfolio_pnl: bool,
) -> None:
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
            {
                "application_sequence": 1,
                "applied_at": fill.fill_time,
                "version": 1,
                "application_count": 1,
            },
        )
    )

    assert verify_paper_recovery_lineage(
        connection, effects, require_portfolio_pnl=require_portfolio_pnl,
    ) is True
    assert _FakePortfolioRepository.loaded_portfolios == [
        (portfolio_id, effects[-1].job_run_id)
    ]


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


def test_verifier_rejects_portfolio_pnl_without_durable_fill_application() -> None:
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
            None,
        )
    )

    assert verify_paper_recovery_lineage(connection, effects) is False


def test_verifier_rejects_portfolio_application_that_predates_fill() -> None:
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
            {
                "application_sequence": 1,
                "applied_at": fill.fill_time - timedelta(microseconds=1),
                "version": 1,
                "application_count": 1,
            },
        )
    )

    assert verify_paper_recovery_lineage(connection, effects) is False


def test_verifier_rejects_portfolio_application_beyond_materialized_version() -> None:
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
            {
                "application_sequence": 2,
                "applied_at": fill.fill_time,
                "version": 1,
                "application_count": 2,
            },
        )
    )

    assert verify_paper_recovery_lineage(connection, effects) is False


def test_verifier_rejects_portfolio_version_ahead_of_application_history() -> None:
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
            {
                "application_sequence": 1,
                "applied_at": fill.fill_time,
                "version": 2,
                "application_count": 1,
            },
        )
    )

    assert verify_paper_recovery_lineage(connection, effects) is False


def test_verifier_rejects_inconsistent_full_portfolio_accounting_history() -> None:
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
    _FakePortfolioRepository.load_error = RuntimeError(
        "PAPER_PORTFOLIO_MATERIALIZED_STATE_INCONSISTENT"
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
            {
                "application_sequence": 1,
                "applied_at": fill.fill_time,
                "version": 1,
                "application_count": 1,
            },
        )
    )

    assert verify_paper_recovery_lineage(connection, effects) is False
    assert _FakePortfolioRepository.loaded_portfolios == [
        (portfolio_id, effects[-1].job_run_id)
    ]


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


@pytest.mark.parametrize(
    ("tamper", "expected"),
    [
        (None, True),
        ("missing", False),
        ("under_cancelled", False),
        ("over_cancelled", False),
        ("before_fill", False),
        ("wrong_outcome", False),
        ("wrong_hash", False),
        ("wrong_order", False),
    ],
)
def test_legacy_partial_fill_cancellation_requires_exact_terminal_evidence(
    tamper, expected,
) -> None:
    """Legacy position PNL must not bypass partial-fill cancellation proof."""
    effects, signal, order, risk, full_fill, pnl_event = _filled_fixture()
    fill = Fill(
        fill_id=full_fill.fill_id,
        order_id=full_fill.order_id,
        signal_id=full_fill.signal_id,
        instrument_id=full_fill.instrument_id,
        side=full_fill.side,
        quantity=Decimal("4"),
        price=full_fill.price,
        commission=full_fill.commission,
        slippage=full_fill.slippage,
        cost_model_version=full_fill.cost_model_version,
        fill_time=full_fill.fill_time,
    )
    cancellation = ExecutionCancellation(
        order.order_id, signal.signal_id, signal.instrument_id,
        Environment.PAPER, "TEST_PARTIAL_CANCEL",
        fill.fill_time + timedelta(seconds=2), Decimal("6"),
    )
    cancellation_effect = _effect(
        effects[0].job_run_id, PaperEffectType.CANCELLATION,
        uuid4() if tamper == "wrong_order" else order.order_id,
        "f" * 64 if tamper == "wrong_hash" else paper_terminal_payload_hash(cancellation),
    )
    effects = effects[:3] + (
        _effect(
            effects[0].job_run_id, PaperEffectType.FILL,
            fill.fill_id, paper_fill_payload_hash(fill),
        ),
        effects[4],
        cancellation_effect,
    )
    terminal = {
        "outcome": "REJECTED" if tamper == "wrong_outcome" else "CANCELLED",
        "reason_code": cancellation.reason_code,
        "event_time": (
            fill.fill_time - timedelta(seconds=1)
            if tamper == "before_fill" else cancellation.cancellation_time
        ),
        "cancelled_quantity": (
            Decimal("5") if tamper == "under_cancelled"
            else Decimal("7") if tamper == "over_cancelled"
            else cancellation.cancelled_quantity
        ),
    }
    if tamper == "missing":
        terminal = None
    connection = _LineageConnection((
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
        terminal,
    ))
    assert verify_paper_recovery_lineage(connection, effects) is expected
    assert _FakePortfolioRepository.loaded_portfolios == []



def test_verifier_hydrates_succeeded_sources_for_staged_fill_accounting() -> None:
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
    local_job_run_id = uuid4()
    local_effects = (
        _effect(
            local_job_run_id,
            PaperEffectType.FILL,
            fill.fill_id,
            paper_fill_payload_hash(fill),
        ),
        _effect(
            local_job_run_id,
            PaperEffectType.PNL,
            pnl_event.pnl_event_id,
            paper_portfolio_pnl_payload_hash(pnl_event),
        ),
    )
    connection = _LineageConnection(
        (
            {"order_id": order.order_id, "signal_id": signal.signal_id},
            _recoverable_effect_row(effects[0]),
            _recoverable_effect_row(effects[1]),
            _recoverable_effect_row(effects[2]),
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
            {
                "application_sequence": 1,
                "applied_at": fill.fill_time,
                "version": 1,
                "application_count": 1,
            },
        )
    )

    assert verify_paper_recovery_lineage(
        connection, local_effects, require_portfolio_pnl=True,
    ) is True
    assert _FakePortfolioRepository.loaded_portfolios == [
        (portfolio_id, local_job_run_id)
    ]


def test_staged_fill_accounting_rejects_failed_source_owner() -> None:
    effects, signal, order, _, fill, _ = _filled_fixture()
    local_effects = (
        _effect(uuid4(), PaperEffectType.FILL, fill.fill_id, paper_fill_payload_hash(fill)),
        _effect(uuid4(), PaperEffectType.PNL, uuid4(), "f" * 64),
    )
    failed_signal = _recoverable_effect_row(effects[0])
    failed_signal["owner_status"] = "FAILED"
    connection = _LineageConnection(
        (
            {"order_id": order.order_id, "signal_id": signal.signal_id},
            failed_signal,
        )
    )

    assert verify_paper_recovery_lineage(
        connection, local_effects, require_portfolio_pnl=True,
    ) is False


def test_verifier_hydrates_succeeded_signal_for_staged_entry() -> None:
    effects, signal, order, risk, _, _ = _filled_fixture()
    local_job_run_id = uuid4()
    local_effects = (
        _effect(
            local_job_run_id,
            PaperEffectType.RISK,
            signal.signal_id,
            paper_risk_payload_hash(risk),
        ),
        _effect(
            local_job_run_id,
            PaperEffectType.ORDER,
            order.order_id,
            paper_order_payload_hash(order),
        ),
    )
    connection = _LineageConnection(
        (
            {"signal_id": signal.signal_id},
            _recoverable_effect_row(effects[0]),
            _signal_row(signal),
            _risk_row(risk),
            _order_row(order),
        )
    )

    assert verify_paper_recovery_lineage(
        connection, local_effects, require_portfolio_pnl=True,
    ) is True
    assert _FakePortfolioRepository.loaded_portfolios == []


@pytest.mark.parametrize(
    "tamper",
    ["missing_source", "failed_source", "wrong_source_hash", "wrong_order_signal"],
)
def test_staged_entry_rejects_unauthenticated_signal_source(tamper) -> None:
    effects, signal, order, risk, _, _ = _filled_fixture()
    local_job_run_id = uuid4()
    local_effects = (
        _effect(
            local_job_run_id,
            PaperEffectType.RISK,
            signal.signal_id,
            paper_risk_payload_hash(risk),
        ),
        _effect(
            local_job_run_id,
            PaperEffectType.ORDER,
            order.order_id,
            paper_order_payload_hash(order),
        ),
    )
    dependency_signal_id = (
        uuid4() if tamper == "wrong_order_signal" else signal.signal_id
    )
    rows = [{"signal_id": dependency_signal_id}]
    if tamper != "wrong_order_signal":
        if tamper == "missing_source":
            rows.append(None)
        else:
            source = _recoverable_effect_row(effects[0])
            if tamper == "failed_source":
                source["owner_status"] = "FAILED"
            elif tamper == "wrong_source_hash":
                source["payload_hash"] = "f" * 64
            rows.append(source)
            if tamper == "wrong_source_hash":
                rows.append(_signal_row(signal))

    assert verify_paper_recovery_lineage(
        _LineageConnection(tuple(rows)),
        local_effects,
        require_portfolio_pnl=True,
    ) is False
