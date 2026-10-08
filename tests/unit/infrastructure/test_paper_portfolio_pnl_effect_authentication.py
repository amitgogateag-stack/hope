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
