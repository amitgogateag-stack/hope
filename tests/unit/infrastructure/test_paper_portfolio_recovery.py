from datetime import UTC, datetime
from uuid import uuid4

import pytest

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper.effects import PaperEffectType, create_paper_effect
from hope.infrastructure.repositories.paper_portfolio import (
    SqlAlchemyPaperPortfolioRepository,
)


class _Effects:
    def __init__(self, *, recoverable=None, reusable=None, conflict=False):
        self.recoverable = recoverable
        self.reusable = reusable
        self.conflict = conflict
        self.calls = []

    def get_recoverable(self, effect_type, entity_id):
        self.calls.append(("recoverable", effect_type, entity_id))
        if self.conflict:
            raise ValueError("PAPER_EFFECT_OWNER_NOT_RECOVERABLE")
        return self.recoverable

    def get_reusable_for_job(self, effect_type, entity_id, job_run_id):
        self.calls.append(("reusable", effect_type, entity_id, job_run_id))
        if self.conflict:
            raise ValueError("PAPER_EFFECT_OWNER_NOT_RECOVERABLE")
        return self.reusable


def _repository(effects):
    repository = SqlAlchemyPaperPortfolioRepository(object())
    repository._effects = effects
    return repository


def test_accounting_history_locks_portfolio_by_default(monkeypatch) -> None:
    repository = _repository(_Effects())
    portfolio_id = uuid4()
    calls = []

    def load_materialized(requested_id, *, lock_for_update):
        calls.append((requested_id, lock_for_update))
        return None

    monkeypatch.setattr(
        repository,
        "_load_materialized_ledger",
        load_materialized,
    )

    assert repository.verify_accounting_history(portfolio_id) is None
    assert calls == [(portfolio_id, True)]


@pytest.mark.parametrize(
    "operation",
    (
        lambda repository, portfolio_id: repository.load_ledger(
            portfolio_id,
            lock_for_update=False,
        ),
        lambda repository, portfolio_id: repository.load_fill_transition(
            portfolio_id,
            uuid4(),
            lock_for_update=False,
        ),
        lambda repository, portfolio_id: repository.verify_accounting_history(
            portfolio_id,
            lock_for_update=False,
        ),
    ),
)
def test_public_portfolio_recovery_rejects_lock_opt_out(
    monkeypatch,
    operation,
) -> None:
    repository = _repository(_Effects())
    monkeypatch.setattr(
        repository,
        "_load_materialized_ledger",
        lambda *args, **kwargs: pytest.fail("unlocked recovery must not read state"),
    )

    with pytest.raises(
        ValueError,
        match="PAPER_PORTFOLIO_RECOVERY_LOCK_REQUIRED",
    ):
        operation(repository, uuid4())


def test_public_portfolio_recovery_fails_closed_on_missing_execution_effect() -> None:
    entity_id = uuid4()
    effects = _Effects(recoverable=None)

    with pytest.raises(
        RuntimeError,
        match="PAPER_PORTFOLIO_EXECUTION_EFFECT_NOT_RECOVERABLE",
    ):
        _repository(effects)._require_recoverable_execution_effect(
            PaperEffectType.ORDER,
            entity_id,
            current_job_run_id=None,
            expected_payload_hash="a" * 64,
        )

    assert effects.calls == [("recoverable", PaperEffectType.ORDER, entity_id)]


def test_inflight_portfolio_recovery_fails_closed_on_conflicting_owner() -> None:
    entity_id, job_run_id = uuid4(), uuid4()
    effects = _Effects(conflict=True)

    with pytest.raises(
        RuntimeError,
        match="PAPER_PORTFOLIO_EXECUTION_OWNER_NOT_RECOVERABLE",
    ):
        _repository(effects)._require_recoverable_execution_effect(
            PaperEffectType.FILL,
            entity_id,
            current_job_run_id=job_run_id,
            expected_payload_hash="a" * 64,
        )

    assert effects.calls == [
        ("reusable", PaperEffectType.FILL, entity_id, job_run_id)
    ]


def test_portfolio_recovery_rejects_recoverable_effect_with_wrong_payload() -> None:
    run = create_scheduled_job_run(
        "paper-portfolio-payload",
        datetime(2026, 10, 8, tzinfo=UTC),
    )
    entity_id = uuid4()
    effect = create_paper_effect(
        run,
        PaperEffectType.SIGNAL,
        entity_id,
        "b" * 64,
    )
    effects = _Effects(recoverable=effect)

    with pytest.raises(
        RuntimeError,
        match="PAPER_PORTFOLIO_EXECUTION_PAYLOAD_MISMATCH",
    ):
        _repository(effects)._require_recoverable_execution_effect(
            PaperEffectType.SIGNAL,
            entity_id,
            current_job_run_id=None,
            expected_payload_hash="a" * 64,
        )

def test_portfolio_recovery_rejects_pnl_effect_with_conflicting_payload() -> None:
    from decimal import Decimal
    from hope.application.paper.portfolio_pnl import (
        PaperPortfolioPnLEvent,
        paper_portfolio_pnl_event_id,
        paper_portfolio_pnl_payload_hash,
    )

    run = create_scheduled_job_run("paper-pnl-payload", datetime(2026, 10, 8, tzinfo=UTC))
    portfolio_id, fill_id, instrument_id = uuid4(), uuid4(), uuid4()
    event = PaperPortfolioPnLEvent(
        pnl_event_id=paper_portfolio_pnl_event_id(portfolio_id, fill_id),
        portfolio_id=portfolio_id,
        fill_id=fill_id,
        instrument_id=instrument_id,
        realized_pnl_delta=Decimal("12.50"),
        commission_delta=Decimal("1.25"),
        event_time=datetime(2026, 10, 8, tzinfo=UTC),
    )
    valid = create_paper_effect(
        run, PaperEffectType.PNL, event.pnl_event_id,
        paper_portfolio_pnl_payload_hash(event),
    )
    _repository(_Effects())._assert_pnl_effect_matches_event(valid, event)
    forged = create_paper_effect(
        run, PaperEffectType.PNL, event.pnl_event_id, "f" * 64,
    )
    with pytest.raises(RuntimeError, match="PAPER_PORTFOLIO_PNL_EFFECT_PAYLOAD_MISMATCH"):
        _repository(_Effects())._assert_pnl_effect_matches_event(forged, event)


def test_portfolio_recovery_rejects_pnl_effect_with_conflicting_identity() -> None:
    from decimal import Decimal
    from hope.application.paper.portfolio_pnl import (
        PaperPortfolioPnLEvent,
        paper_portfolio_pnl_event_id,
        paper_portfolio_pnl_payload_hash,
    )

    run = create_scheduled_job_run("paper-pnl-identity", datetime(2026, 10, 8, tzinfo=UTC))
    portfolio_id, fill_id, instrument_id = uuid4(), uuid4(), uuid4()
    event = PaperPortfolioPnLEvent(
        pnl_event_id=paper_portfolio_pnl_event_id(portfolio_id, fill_id),
        portfolio_id=portfolio_id,
        fill_id=fill_id,
        instrument_id=instrument_id,
        realized_pnl_delta=Decimal("0"),
        commission_delta=Decimal("0"),
        event_time=datetime(2026, 10, 8, tzinfo=UTC),
    )
    wrong_identity = create_paper_effect(
        run, PaperEffectType.PNL, uuid4(), paper_portfolio_pnl_payload_hash(event),
    )
    with pytest.raises(RuntimeError, match="PAPER_PORTFOLIO_PNL_EFFECT_IDENTITY_MISMATCH"):
        _repository(_Effects())._assert_pnl_effect_matches_event(wrong_identity, event)


def test_portfolio_recovery_rejects_non_pnl_effect_even_with_matching_payload() -> None:
    from decimal import Decimal
    from hope.application.paper.portfolio_pnl import (
        PaperPortfolioPnLEvent,
        paper_portfolio_pnl_event_id,
        paper_portfolio_pnl_payload_hash,
    )

    run = create_scheduled_job_run("paper-pnl-type", datetime(2026, 10, 8, tzinfo=UTC))
    portfolio_id, fill_id, instrument_id = uuid4(), uuid4(), uuid4()
    event = PaperPortfolioPnLEvent(
        pnl_event_id=paper_portfolio_pnl_event_id(portfolio_id, fill_id),
        portfolio_id=portfolio_id,
        fill_id=fill_id,
        instrument_id=instrument_id,
        realized_pnl_delta=Decimal("0"),
        commission_delta=Decimal("0"),
        event_time=datetime(2026, 10, 8, tzinfo=UTC),
    )
    wrong_type = create_paper_effect(
        run, PaperEffectType.FILL, event.pnl_event_id,
        paper_portfolio_pnl_payload_hash(event),
    )
    with pytest.raises(RuntimeError, match="PAPER_PORTFOLIO_PNL_EFFECT_TYPE_MISMATCH"):
        _repository(_Effects())._assert_pnl_effect_matches_event(wrong_type, event)


@pytest.mark.parametrize("wrong_type", [PaperEffectType.SIGNAL, PaperEffectType.FILL])
def test_portfolio_recovery_rejects_wrong_execution_effect_type(wrong_type) -> None:
    run = create_scheduled_job_run("paper-execution-type", datetime(2026, 10, 8, tzinfo=UTC))
    entity_id = uuid4()
    effect = create_paper_effect(run, wrong_type, entity_id, "a" * 64)
    with pytest.raises(RuntimeError, match="PAPER_PORTFOLIO_EXECUTION_EFFECT_IDENTITY_MISMATCH"):
        _repository(_Effects(recoverable=effect))._require_recoverable_execution_effect(
            PaperEffectType.ORDER, entity_id,
            current_job_run_id=None, expected_payload_hash="a" * 64,
        )


def test_portfolio_recovery_rejects_wrong_execution_effect_entity() -> None:
    run = create_scheduled_job_run("paper-execution-entity", datetime(2026, 10, 8, tzinfo=UTC))
    entity_id = uuid4()
    effect = create_paper_effect(run, PaperEffectType.ORDER, uuid4(), "a" * 64)
    with pytest.raises(RuntimeError, match="PAPER_PORTFOLIO_EXECUTION_EFFECT_IDENTITY_MISMATCH"):
        _repository(_Effects(recoverable=effect))._require_recoverable_execution_effect(
            PaperEffectType.ORDER, entity_id,
            current_job_run_id=None, expected_payload_hash="a" * 64,
        )


def test_portfolio_recovery_rejects_execution_effect_with_forged_effect_id() -> None:
    run = create_scheduled_job_run("paper-forged-effect-id", datetime(2026, 10, 8, tzinfo=UTC))
    entity_id = uuid4()
    valid = create_paper_effect(run, PaperEffectType.ORDER, entity_id, "a" * 64)
    # Simulate a malformed repository result bypassing the immutable dataclass validator.
    forged = object.__new__(type(valid))
    for field in ("job_run_id", "effect_type", "entity_id", "payload_hash"):
        object.__setattr__(forged, field, getattr(valid, field))
    object.__setattr__(forged, "effect_id", uuid4())
    with pytest.raises(RuntimeError, match="PAPER_PORTFOLIO_EXECUTION_EFFECT_IDENTITY_MISMATCH"):
        _repository(_Effects(recoverable=forged))._require_recoverable_execution_effect(
            PaperEffectType.ORDER, entity_id,
            current_job_run_id=None, expected_payload_hash="a" * 64,
        )
