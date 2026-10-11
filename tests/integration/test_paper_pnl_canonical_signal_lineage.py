"""Database-boundary authentication of canonical PAPER PNL signal lineage."""
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
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository


@pytest.mark.integration
def test_canonical_position_pnl_rejects_untracked_source_and_accepts_atomic_lineage():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    when = datetime(2026, 10, 11, 13, 0, tzinfo=timezone.utc)
    signal_id, instrument_id, position_id = uuid4(), uuid4(), uuid4()
    run = create_scheduled_job_run(f"pnl-canonical-lineage:{uuid4()}", when)
    event = PaperPnLEvent(uuid4(), position_id, Decimal("1.25"), when)
    try:
        with engine.begin() as connection:
            apply_migrations(connection, Path(__file__).parents[2] / "migrations")
            connection.execute(text(
                "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                "VALUES (:instrument_id, :symbol, 'TEST', 'ACTIVE')"
            ), {"instrument_id": instrument_id, "symbol": f"PNL-{instrument_id}"})
            connection.execute(text(
                "INSERT INTO signals(signal_id, instrument_id, decision_time, state, "
                "strategy_version, signal_type, conviction, inputs_hash) "
                "VALUES (:signal_id, :instrument_id, :when, 'SIGNAL', "
                "'canonical-v1', 'ENTRY', 0.5, :hash)"
            ), {"signal_id": signal_id, "instrument_id": instrument_id,
                "when": when, "hash": "a" * 64})
            connection.execute(text(
                "INSERT INTO positions(position_id, instrument_id, opened_from_signal_id, "
                "quantity, opened_at) VALUES (:position_id, :instrument_id, :signal_id, 1, :when)"
            ), {"position_id": position_id, "instrument_id": instrument_id,
                "signal_id": signal_id, "when": when})
            assert SqlAlchemyJobRunRepository(connection).claim(run) is True

        def write_accounting(connection):
            assert SqlAlchemyPaperEffectRepository(connection).record(
                create_paper_effect(
                    run, PaperEffectType.PNL, event.pnl_event_id,
                    paper_pnl_payload_hash(event)
                )
            ) is True
            connection.execute(text(
                "INSERT INTO pnl_events(pnl_event_id, position_id, amount, event_time) "
                "VALUES (:id, :position_id, :amount, :when)"
            ), {"id": event.pnl_event_id, "position_id": position_id,
                "amount": event.amount, "when": event.event_time})

        with pytest.raises(IntegrityError, match="PAPER_PNL_CANONICAL_SOURCE_SIGNAL_UNTRACKED"):
            with engine.begin() as connection:
                write_accounting(connection)

        with engine.begin() as connection:
            assert connection.execute(text(
                "SELECT count(*) FROM pnl_events WHERE pnl_event_id=:id"
            ), {"id": event.pnl_event_id}).scalar_one() == 0
            assert connection.execute(text(
                "SELECT count(*) FROM paper_effects WHERE effect_type='PNL' AND entity_id=:id"
            ), {"id": event.pnl_event_id}).scalar_one() == 0

        with engine.begin() as connection:
            assert SqlAlchemyPaperEffectRepository(connection).record(
                create_paper_effect(run, PaperEffectType.SIGNAL, signal_id, "b" * 64)
            ) is True
            write_accounting(connection)

        with engine.begin() as connection:
            assert connection.execute(text(
                "SELECT count(*) FROM pnl_events WHERE pnl_event_id=:id"
            ), {"id": event.pnl_event_id}).scalar_one() == 1
    finally:
        engine.dispose()
