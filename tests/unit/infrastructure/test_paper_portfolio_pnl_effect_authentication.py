from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper.effects import PaperEffectType, create_paper_effect
from hope.application.paper.portfolio_pnl import (
    PaperPortfolioPnLEvent,
    paper_portfolio_pnl_event_id,
    paper_portfolio_pnl_payload_hash,
)
from hope.infrastructure.repositories.paper_portfolio_pnl import SqlAlchemyPaperPortfolioPnLRepository


class _Effects:
    def __init__(self, effect):
        self.effect = effect
        self.fill_checked = False

    def get(self, effect_type, entity_id):
        assert effect_type is PaperEffectType.PNL
        return self.effect

    def get_reusable_for_job(self, *args):
        self.fill_checked = True
        pytest.fail("invalid PNL effect must fail before fill lineage lookup")


def _event():
    portfolio_id, fill_id, instrument_id = uuid4(), uuid4(), uuid4()
    return PaperPortfolioPnLEvent(
        pnl_event_id=paper_portfolio_pnl_event_id(portfolio_id, fill_id),
        portfolio_id=portfolio_id,
        fill_id=fill_id,
        instrument_id=instrument_id,
        realized_pnl_delta=Decimal("0"),
        commission_delta=Decimal("0"),
        event_time=datetime(2026, 10, 8, tzinfo=UTC),
    )


@pytest.mark.parametrize("wrong_type", [PaperEffectType.FILL, PaperEffectType.ORDER])
def test_pnl_read_rejects_wrong_effect_type_before_lineage_lookup(wrong_type):
    event = _event()
    run = create_scheduled_job_run("pnl-read-type", datetime(2026, 10, 8, tzinfo=UTC))
    effect = create_paper_effect(
        run, wrong_type, event.pnl_event_id, paper_portfolio_pnl_payload_hash(event),
    )
    repository = object.__new__(SqlAlchemyPaperPortfolioPnLRepository)
    repository._effects = _Effects(effect)
    repository._get_row = lambda event_id: vars(event)
    with pytest.raises(ValueError, match="PAPER_PORTFOLIO_PNL_EFFECT_IDENTITY_CONFLICT"):
        repository.get(event.portfolio_id, event.fill_id)
    assert repository._effects.fill_checked is False


def test_pnl_read_rejects_wrong_effect_entity_before_lineage_lookup():
    event = _event()
    run = create_scheduled_job_run("pnl-read-entity", datetime(2026, 10, 8, tzinfo=UTC))
    effect = create_paper_effect(
        run, PaperEffectType.PNL, uuid4(), paper_portfolio_pnl_payload_hash(event),
    )
    repository = object.__new__(SqlAlchemyPaperPortfolioPnLRepository)
    repository._effects = _Effects(effect)
    repository._get_row = lambda event_id: vars(event)
    with pytest.raises(ValueError, match="PAPER_PORTFOLIO_PNL_EFFECT_IDENTITY_CONFLICT"):
        repository.get(event.portfolio_id, event.fill_id)
    assert repository._effects.fill_checked is False


@pytest.mark.parametrize("wrong_type", [PaperEffectType.ORDER, PaperEffectType.PNL])
def test_pnl_read_rejects_wrong_source_fill_effect_type(wrong_type):
    event = _event()
    run = create_scheduled_job_run("pnl-source-fill-type", datetime(2026, 10, 8, tzinfo=UTC))
    effect = create_paper_effect(
        run, PaperEffectType.PNL, event.pnl_event_id,
        paper_portfolio_pnl_payload_hash(event),
    )
    source = create_paper_effect(run, wrong_type, event.fill_id, "a" * 64)

    class _WithSource:
        def get(self, effect_type, entity_id):
            return effect

        def get_reusable_for_job(self, effect_type, entity_id, job_run_id):
            assert (effect_type, entity_id, job_run_id) == (
                PaperEffectType.FILL, event.fill_id, run.job_run_id,
            )
            return source

    repository = object.__new__(SqlAlchemyPaperPortfolioPnLRepository)
    repository._effects = _WithSource()
    repository._get_row = lambda event_id: vars(event)
    with pytest.raises(ValueError, match="PAPER_PORTFOLIO_PNL_SOURCE_FILL_LINEAGE_CONFLICT"):
        repository.get(event.portfolio_id, event.fill_id)


def test_pnl_read_rejects_wrong_source_fill_entity():
    event = _event()
    run = create_scheduled_job_run("pnl-source-fill-entity", datetime(2026, 10, 8, tzinfo=UTC))
    effect = create_paper_effect(
        run, PaperEffectType.PNL, event.pnl_event_id,
        paper_portfolio_pnl_payload_hash(event),
    )
    source = create_paper_effect(run, PaperEffectType.FILL, uuid4(), "a" * 64)

    class _WithSource:
        def get(self, effect_type, entity_id):
            return effect

        def get_reusable_for_job(self, effect_type, entity_id, job_run_id):
            return source

    repository = object.__new__(SqlAlchemyPaperPortfolioPnLRepository)
    repository._effects = _WithSource()
    repository._get_row = lambda event_id: vars(event)
    with pytest.raises(ValueError, match="PAPER_PORTFOLIO_PNL_SOURCE_FILL_LINEAGE_CONFLICT"):
        repository.get(event.portfolio_id, event.fill_id)


@pytest.mark.parametrize("wrong_type", [PaperEffectType.ORDER, PaperEffectType.PNL])
def test_pnl_persist_rejects_wrong_source_fill_effect_type(wrong_type):
    event = _event()
    run = create_scheduled_job_run("pnl-persist-source-type", datetime(2026, 10, 8, tzinfo=UTC))
    effect = create_paper_effect(
        run, PaperEffectType.PNL, event.pnl_event_id,
        paper_portfolio_pnl_payload_hash(event),
    )
    source = create_paper_effect(run, wrong_type, event.fill_id, "a" * 64)

    class _Connection:
        def begin_nested(self):
            from contextlib import nullcontext
            return nullcontext()

        def execute(self, query):
            class _Result:
                def scalar_one_or_none(self):
                    return event.fill_id
            return _Result()

    class _WithSource:
        def get_reusable_for_job(self, *args):
            return source

        def record(self, effect):
            pytest.fail("invalid source must not persist PNL effect")

    repository = object.__new__(SqlAlchemyPaperPortfolioPnLRepository)
    repository._connection = _Connection()
    repository._effects = _WithSource()
    from sqlalchemy import Column, MetaData, Table, Uuid

    repository._applications = Table(
        "paper_portfolio_fill_applications", MetaData(),
        Column("portfolio_id", Uuid), Column("fill_id", Uuid),
    )
    with pytest.raises(ValueError, match="PAPER_PORTFOLIO_PNL_SOURCE_FILL_UNTRACKED"):
        repository.persist(effect, event)


@pytest.mark.parametrize("mismatch", ["portfolio", "fill", "event_id"])
def test_pnl_read_rejects_row_not_matching_requested_identity(mismatch):
    event = _event()
    row = vars(event).copy()
    if mismatch == "portfolio":
        row["portfolio_id"] = uuid4()
    elif mismatch == "fill":
        row["fill_id"] = uuid4()
    else:
        row["pnl_event_id"] = uuid4()

    class _NoEffects:
        def get(self, *args):
            pytest.fail("mismatched durable row must be rejected before effect lookup")

    repository = object.__new__(SqlAlchemyPaperPortfolioPnLRepository)
    repository._effects = _NoEffects()
    repository._get_row = lambda event_id: row
    with pytest.raises(ValueError, match="PAPER_PORTFOLIO_PNL_IDENTITY_MISMATCH"):
        repository.get(event.portfolio_id, event.fill_id)



def test_pnl_read_rejects_event_without_applied_fill():
    event = _event()
    run = create_scheduled_job_run(
        "pnl-read-orphan", datetime(2026, 10, 8, tzinfo=UTC),
    )
    pnl_effect = create_paper_effect(
        run, PaperEffectType.PNL, event.pnl_event_id,
        paper_portfolio_pnl_payload_hash(event),
    )
    fill_effect = create_paper_effect(
        run, PaperEffectType.FILL, event.fill_id, "a" * 64,
    )

    class _ValidEffects:
        def get(self, effect_type, entity_id):
            return pnl_effect

        def get_reusable_for_job(self, effect_type, entity_id, job_run_id):
            return fill_effect

    class _MissingApplication:
        def execute(self, statement):
            class _Result:
                def scalar_one_or_none(self):
                    return None
            return _Result()

    from sqlalchemy import Column, MetaData, Table, Uuid

    repository = object.__new__(SqlAlchemyPaperPortfolioPnLRepository)
    repository._effects = _ValidEffects()
    repository._connection = _MissingApplication()
    repository._applications = Table(
        "paper_portfolio_fill_applications", MetaData(),
        Column("portfolio_id", Uuid), Column("fill_id", Uuid),
    )
    repository._get_row = lambda event_id: vars(event)
    with pytest.raises(RuntimeError, match="PAPER_PORTFOLIO_PNL_EVENT_WITHOUT_APPLICATION"):
        repository.get(event.portfolio_id, event.fill_id)


@pytest.mark.parametrize("effect_exists", [False, True])
def test_pnl_read_missing_event_authenticates_durable_effect(effect_exists):
    event = _event()
    run = create_scheduled_job_run(
        "pnl-missing-row", datetime(2026, 10, 8, tzinfo=UTC),
    )
    effect = create_paper_effect(
        run, PaperEffectType.PNL, event.pnl_event_id,
        paper_portfolio_pnl_payload_hash(event),
    )

    class _EffectsForMissingRow:
        def get(self, effect_type, entity_id):
            assert effect_type is PaperEffectType.PNL
            assert entity_id == event.pnl_event_id
            return effect if effect_exists else None

    repository = object.__new__(SqlAlchemyPaperPortfolioPnLRepository)
    repository._effects = _EffectsForMissingRow()
    repository._get_row = lambda event_id: None
    class _NoApplication:
        def execute(self, query):
            class _Result:
                def scalar_one_or_none(self):
                    return None
            return _Result()
    from sqlalchemy import Column, MetaData, Table, Uuid
    repository._connection = _NoApplication()
    repository._applications = Table(
        "paper_portfolio_fill_applications", MetaData(),
        Column("portfolio_id", Uuid), Column("fill_id", Uuid),
    )
    if effect_exists:
        with pytest.raises(RuntimeError, match="PAPER_PORTFOLIO_PNL_EFFECT_WITHOUT_EVENT"):
            repository.get(event.portfolio_id, event.fill_id)
    else:
        assert repository.get(event.portfolio_id, event.fill_id) is None


def test_pnl_event_rejects_noncanonical_identity_at_domain_boundary():
    canonical = _event()
    with pytest.raises(ValueError, match="PAPER_PORTFOLIO_PNL_IDENTITY_MISMATCH"):
        PaperPortfolioPnLEvent(
            pnl_event_id=uuid4(), portfolio_id=canonical.portfolio_id,
            fill_id=canonical.fill_id, instrument_id=canonical.instrument_id,
            realized_pnl_delta=canonical.realized_pnl_delta,
            commission_delta=canonical.commission_delta,
            event_time=canonical.event_time,
        )


def test_pnl_persist_rejects_effect_with_wrong_event_identity_before_db_access():
    event = _event()
    run = create_scheduled_job_run("pnl-effect-identity", datetime(2026, 10, 8, tzinfo=UTC))
    effect = create_paper_effect(
        run, PaperEffectType.PNL, uuid4(), paper_portfolio_pnl_payload_hash(event),
    )
    repository = object.__new__(SqlAlchemyPaperPortfolioPnLRepository)
    with pytest.raises(ValueError, match="PAPER_PORTFOLIO_PNL_EFFECT_MISMATCH"):
        repository.persist(effect, event)


def test_pnl_read_rejects_applied_fill_without_event_or_effect():
    event = _event()
    class _NoPnLEffect:
        def get(self, effect_type, entity_id):
            assert (effect_type, entity_id) == (PaperEffectType.PNL, event.pnl_event_id)
            return None

    class _AppliedFill:
        def execute(self, statement):
            class _Result:
                def scalar_one_or_none(self):
                    return event.fill_id
            return _Result()

    from sqlalchemy import Column, MetaData, Table, Uuid
    repository = object.__new__(SqlAlchemyPaperPortfolioPnLRepository)
    repository._effects = _NoPnLEffect()
    repository._connection = _AppliedFill()
    repository._applications = Table(
        "paper_portfolio_fill_applications", MetaData(),
        Column("portfolio_id", Uuid), Column("fill_id", Uuid),
    )
    repository._get_row = lambda event_id: None
    with pytest.raises(RuntimeError, match="PAPER_PORTFOLIO_APPLIED_FILL_WITHOUT_PNL"):
        repository.get(event.portfolio_id, event.fill_id)


def test_pnl_read_rejects_forged_effect_id_before_fill_lineage_lookup():
    event = _event()
    run = create_scheduled_job_run("pnl-forged-effect", datetime(2026, 10, 8, tzinfo=UTC))
    valid = create_paper_effect(
        run, PaperEffectType.PNL, event.pnl_event_id,
        paper_portfolio_pnl_payload_hash(event),
    )
    forged = object.__new__(type(valid))
    for field in ("job_run_id", "effect_type", "entity_id", "payload_hash"):
        object.__setattr__(forged, field, getattr(valid, field))
    object.__setattr__(forged, "effect_id", uuid4())
    repository = object.__new__(SqlAlchemyPaperPortfolioPnLRepository)
    repository._effects = _Effects(forged)
    repository._get_row = lambda event_id: vars(event)
    with pytest.raises(ValueError, match="PAPER_PORTFOLIO_PNL_EFFECT_IDENTITY_CONFLICT"):
        repository.get(event.portfolio_id, event.fill_id)
    assert repository._effects.fill_checked is False
