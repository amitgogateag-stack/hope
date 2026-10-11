"""Transaction-boundary completeness for legacy position P&L evidence."""

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


UTC = timezone.utc


def _insert_position(connection, *, position_id, instrument_id, signal_id, event_time):
    connection.execute(
        text(
            "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
            "VALUES (:instrument_id, :symbol, 'TEST', 'ACTIVE')"
        ),
        {"instrument_id": instrument_id, "symbol": f"PNL-{instrument_id}"},
    )
    connection.execute(
        text(
            "INSERT INTO signals(signal_id, instrument_id, decision_time, state) "
            "VALUES (:signal_id, :instrument_id, :event_time, 'SIGNAL')"
        ),
        {
            "signal_id": signal_id,
            "instrument_id": instrument_id,
            "event_time": event_time,
        },
    )
    connection.execute(
        text(
            "INSERT INTO positions(position_id, instrument_id, opened_from_signal_id, "
            "quantity, opened_at) VALUES ("
            ":position_id, :instrument_id, :signal_id, 1, :event_time)"
        ),
        {
            "position_id": position_id,
            "instrument_id": instrument_id,
            "signal_id": signal_id,
            "event_time": event_time,
        },
    )


@pytest.mark.integration
def test_legacy_pnl_event_without_effect_cannot_commit_and_rolls_back() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    event_id = uuid4()
    position_id = uuid4()
    try:
        with engine.begin() as connection:
            apply_migrations(connection, Path(__file__).parents[2] / "migrations")
            _insert_position(
                connection,
                position_id=position_id,
                instrument_id=uuid4(),
                signal_id=uuid4(),
                event_time=datetime(2026, 10, 11, tzinfo=UTC),
            )

        with pytest.raises(IntegrityError, match="PAPER_PNL_EVENT_WITHOUT_EFFECT"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO pnl_events(pnl_event_id, position_id, amount, event_time) "
                        "VALUES (:event_id, :position_id, 1.25, :event_time)"
                    ),
                    {
                        "event_id": event_id,
                        "position_id": position_id,
                        "event_time": datetime(2026, 10, 11, tzinfo=UTC),
                    },
                )

        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT count(*) FROM pnl_events WHERE pnl_event_id=:event_id"),
                {"event_id": event_id},
            ).scalar_one() == 0
    finally:
        engine.dispose()


@pytest.mark.integration
def test_legacy_pnl_event_and_effect_may_commit_event_first_atomically() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    event_time = datetime(2026, 10, 11, 0, 1, tzinfo=UTC)
    position_id, instrument_id, signal_id = uuid4(), uuid4(), uuid4()
    run = create_scheduled_job_run(f"legacy-pnl-complete:{uuid4()}", event_time)
    event = PaperPnLEvent(uuid4(), position_id, Decimal("2.50"), event_time)
    try:
        with engine.begin() as connection:
            apply_migrations(connection, Path(__file__).parents[2] / "migrations")
            _insert_position(
                connection,
                position_id=position_id,
                instrument_id=instrument_id,
                signal_id=signal_id,
                event_time=event_time,
            )
            assert SqlAlchemyJobRunRepository(connection).claim(run) is True
            connection.execute(
                text(
                    "INSERT INTO pnl_events(pnl_event_id, position_id, amount, event_time) "
                    "VALUES (:event_id, :position_id, :amount, :event_time)"
                ),
                {
                    "event_id": event.pnl_event_id,
                    "position_id": event.position_id,
                    "amount": event.amount,
                    "event_time": event.event_time,
                },
            )
            assert SqlAlchemyPaperEffectRepository(connection).record(
                create_paper_effect(
                    run,
                    PaperEffectType.PNL,
                    event.pnl_event_id,
                    paper_pnl_payload_hash(event),
                )
            ) is True

        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT count(*) FROM pnl_events WHERE pnl_event_id=:event_id"),
                {"event_id": event.pnl_event_id},
            ).scalar_one() == 1
            assert connection.execute(
                text(
                    "SELECT count(*) FROM paper_effects "
                    "WHERE effect_type='PNL' AND entity_id=:event_id"
                ),
                {"event_id": event.pnl_event_id},
            ).scalar_one() == 1
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("event_first", [True, False])
def test_legacy_pnl_event_effect_payload_mismatch_rolls_back_both_orders(
    event_first: bool,
) -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    event_time = datetime(2026, 10, 11, 0, 2, tzinfo=UTC)
    position_id, instrument_id, signal_id = uuid4(), uuid4(), uuid4()
    run = create_scheduled_job_run(
        f"legacy-pnl-payload-conflict:{event_first}:{uuid4()}",
        event_time,
    )
    event = PaperPnLEvent(uuid4(), position_id, Decimal("3.75"), event_time)
    insert_event = text(
        "INSERT INTO pnl_events(pnl_event_id, position_id, amount, event_time) "
        "VALUES (:event_id, :position_id, :amount, :event_time)"
    )
    event_params = {
        "event_id": event.pnl_event_id,
        "position_id": event.position_id,
        "amount": event.amount,
        "event_time": event.event_time,
    }
    try:
        with engine.begin() as connection:
            apply_migrations(connection, Path(__file__).parents[2] / "migrations")
            _insert_position(
                connection,
                position_id=position_id,
                instrument_id=instrument_id,
                signal_id=signal_id,
                event_time=event_time,
            )
            assert SqlAlchemyJobRunRepository(connection).claim(run) is True

            with pytest.raises(
                IntegrityError,
                match="PAPER_PNL_EFFECT_PAYLOAD_MISMATCH",
            ):
                with connection.begin_nested():
                    if event_first:
                        connection.execute(insert_event, event_params)
                    SqlAlchemyPaperEffectRepository(connection).record(
                        create_paper_effect(
                            run,
                            PaperEffectType.PNL,
                            event.pnl_event_id,
                            "f" * 64,
                        )
                    )
                    if not event_first:
                        connection.execute(insert_event, event_params)

            assert connection.execute(
                text("SELECT count(*) FROM pnl_events WHERE pnl_event_id=:event_id"),
                {"event_id": event.pnl_event_id},
            ).scalar_one() == 0
            assert connection.execute(
                text(
                    "SELECT count(*) FROM paper_effects "
                    "WHERE effect_type='PNL' AND entity_id=:event_id"
                ),
                {"event_id": event.pnl_event_id},
            ).scalar_one() == 0
    finally:
        engine.dispose()
