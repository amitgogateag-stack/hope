"""Canonical PAPER position accounting cannot rely on a non-actionable signal."""
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper.effects import PaperEffectType, create_paper_effect
from hope.application.paper.pnl import PaperPnLEvent, paper_pnl_payload_hash
from hope.application.paper.signals import paper_signal_payload_hash
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository


@pytest.mark.integration
@pytest.mark.parametrize("state", ["SIGNAL", "NO_SIGNAL", "STALE_SIGNAL"])
def test_canonical_pnl_requires_actionable_source_signal(state: str) -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    instant = datetime(2026, 10, 11, 17, 0, tzinfo=timezone.utc)
    instrument_id, signal_id, position_id = uuid4(), uuid4(), uuid4()
    run = create_scheduled_job_run(f"pnl-actionable:{state}:{uuid4()}", instant)
    event = PaperPnLEvent(uuid4(), position_id, Decimal("1.25"), instant)
    try:
        with engine.begin() as connection:
            apply_migrations(connection, Path(__file__).parents[2] / "migrations")
            connection.execute(text(
                "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                "VALUES (:id, :symbol, 'TEST', 'ACTIVE')"
            ), {"id": instrument_id, "symbol": f"PNL-ACTION-{instrument_id}"})
            connection.execute(text(
                "INSERT INTO signals(signal_id, instrument_id, decision_time, state, "
                "strategy_version, signal_type, conviction, inputs_hash) "
                "VALUES (:id, :instrument, :time, :state, 'canonical-v1', "
                "'ENTRY', 0.5, :hash)"
            ), {"id": signal_id, "instrument": instrument_id,
                "time": instant, "state": state, "hash": "a" * 64})
            connection.execute(text(
                "INSERT INTO positions(position_id, instrument_id, "
                "opened_from_signal_id, quantity, opened_at) "
                "VALUES (:id, :instrument, :signal, 1, :time)"
            ), {"id": position_id, "instrument": instrument_id,
                "signal": signal_id, "time": instant})
            assert SqlAlchemyJobRunRepository(connection).claim(run) is True
            canonical_signal = Signal(
                signal_id=signal_id,
                instrument_id=instrument_id,
                strategy_version="canonical-v1",
                decision_time=instant,
                signal_type=SignalType.ENTRY,
                conviction=Decimal("0.5"),
                inputs_hash="a" * 64,
            )
            assert SqlAlchemyPaperEffectRepository(connection).record(
                create_paper_effect(
                    run, PaperEffectType.SIGNAL, signal_id,
                    paper_signal_payload_hash(canonical_signal),
                )
            ) is True

        def write_pnl():
            with engine.begin() as connection:
                assert SqlAlchemyPaperEffectRepository(connection).record(
                    create_paper_effect(
                        run, PaperEffectType.PNL, event.pnl_event_id,
                        paper_pnl_payload_hash(event)
                    )
                ) is True
                connection.execute(text(
                    "INSERT INTO pnl_events(pnl_event_id, position_id, amount, event_time) "
                    "VALUES (:id, :position, :amount, :time)"
                ), {"id": event.pnl_event_id, "position": position_id,
                    "amount": event.amount, "time": instant})

        if state == "SIGNAL":
            write_pnl()
        else:
            with pytest.raises(
                IntegrityError,
                match="PAPER_PNL_CANONICAL_SOURCE_SIGNAL_NOT_ACTIONABLE",
            ):
                write_pnl()
        with engine.begin() as connection:
            count = connection.execute(text(
                "SELECT count(*) FROM pnl_events WHERE pnl_event_id=:id"
            ), {"id": event.pnl_event_id}).scalar_one()
            assert count == int(state == "SIGNAL")
            effect_count = connection.execute(text(
                "SELECT count(*) FROM paper_effects "
                "WHERE effect_type='PNL' AND entity_id=:id"
            ), {"id": event.pnl_event_id}).scalar_one()
            assert effect_count == count
    finally:
        engine.dispose()
