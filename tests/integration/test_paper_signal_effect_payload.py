import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper import PaperCycleContext, PaperSignalWriter
from hope.application.paper.effects import PaperEffectType, create_paper_effect
from hope.application.paper.signals import paper_signal_payload_hash
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository


def _setup_identity(connection, migrations_dir):
    apply_migrations(connection, migrations_dir)
    instrument_id = uuid4()
    decision_time = datetime(2026, 10, 13, 2, 0, 0, 123456, tzinfo=timezone.utc)
    run = create_scheduled_job_run(f"paper-signal-payload:{uuid4()}", decision_time)
    context = PaperCycleContext(run)
    connection.execute(
        text(
            "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
            "VALUES (:id, :symbol, 'TEST', 'ACTIVE')"
        ),
        {"id": instrument_id, "symbol": f"SIGNAL-PAYLOAD-{instrument_id.hex[:12]}"},
    )
    assert SqlAlchemyJobRunRepository(connection).claim(run) is True
    signal_id = context.signal_id(
        instrument_id=instrument_id,
        strategy_version="paper-signal-payload-v1",
        decision_time=decision_time,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.7500"),
        inputs_hash="c" * 64,
    )
    signal = Signal(
        signal_id=signal_id,
        instrument_id=instrument_id,
        strategy_version="paper-signal-payload-v1",
        decision_time=decision_time,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.7500"),
        inputs_hash="c" * 64,
    )
    return run, context, signal


@pytest.mark.integration
def test_paper_signal_effect_payload_is_database_authenticated() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"

    with engine.begin() as connection:
        _, context, signal = _setup_identity(connection, migrations_dir)
        assert PaperSignalWriter(
            SqlAlchemyPaperSignalRepository(connection)
        ).record(context, signal) is True
        row = connection.execute(
            text(
                "SELECT effect.payload_hash, hope_paper_signal_payload_hash("
                "source_signal.signal_id, source_signal.instrument_id, "
                "source_signal.strategy_version, source_signal.decision_time, "
                "source_signal.signal_type, source_signal.conviction, "
                "source_signal.inputs_hash) AS database_hash "
                "FROM signals source_signal JOIN paper_effects effect "
                "ON effect.effect_type='SIGNAL' "
                "AND effect.entity_id=source_signal.signal_id "
                "WHERE source_signal.signal_id=:signal_id"
            ),
            {"signal_id": signal.signal_id},
        ).one()
        assert row.payload_hash == paper_signal_payload_hash(signal)
        assert row.database_hash == row.payload_hash

    engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("effect_first", [True, False])
def test_conflicting_paper_signal_pair_rolls_back_in_either_insert_order(
    effect_first: bool,
) -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"

    with engine.begin() as connection:
        run, _, signal = _setup_identity(connection, migrations_dir)
        bad_effect = create_paper_effect(
            run,
            PaperEffectType.SIGNAL,
            signal.signal_id,
            "f" * 64,
        )

        def insert_effect():
            connection.execute(
                text(
                    "INSERT INTO paper_effects("
                    "effect_id, job_run_id, effect_type, entity_id, payload_hash"
                    ") VALUES ("
                    ":effect_id, :job_run_id, 'SIGNAL', :signal_id, :payload_hash"
                    ")"
                ),
                {
                    "effect_id": bad_effect.effect_id,
                    "job_run_id": run.job_run_id,
                    "signal_id": signal.signal_id,
                    "payload_hash": bad_effect.payload_hash,
                },
            )

        def insert_signal():
            connection.execute(
                text(
                    "INSERT INTO signals("
                    "signal_id, instrument_id, decision_time, state, "
                    "strategy_version, signal_type, conviction, inputs_hash"
                    ") VALUES ("
                    ":signal_id, :instrument_id, :decision_time, 'SIGNAL', "
                    ":strategy_version, 'ENTRY', :conviction, :inputs_hash"
                    ")"
                ),
                {
                    "signal_id": signal.signal_id,
                    "instrument_id": signal.instrument_id,
                    "decision_time": signal.decision_time,
                    "strategy_version": signal.strategy_version,
                    "conviction": signal.conviction,
                    "inputs_hash": signal.inputs_hash,
                },
            )

        with pytest.raises(
            IntegrityError,
            match="PAPER_SIGNAL_EFFECT_PAYLOAD_MISMATCH",
        ):
            with connection.begin_nested():
                first, second = (
                    (insert_effect, insert_signal)
                    if effect_first
                    else (insert_signal, insert_effect)
                )
                first()
                second()

        assert connection.execute(
            text("SELECT count(*) FROM signals WHERE signal_id=:signal_id"),
            {"signal_id": signal.signal_id},
        ).scalar_one() == 0
        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_effects "
                "WHERE effect_type='SIGNAL' AND entity_id=:signal_id"
            ),
            {"signal_id": signal.signal_id},
        ).scalar_one() == 0

    engine.dispose()


@pytest.mark.integration
def test_partial_paper_signal_recovery_material_is_rejected() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"

    with engine.begin() as connection:
        run, _, signal = _setup_identity(connection, migrations_dir)
        effect = create_paper_effect(
            run,
            PaperEffectType.SIGNAL,
            signal.signal_id,
            "a" * 64,
        )
        with pytest.raises(
            IntegrityError,
            match="PAPER_SIGNAL_RECOVERY_MATERIAL_PARTIAL",
        ):
            with connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO paper_effects("
                        "effect_id, job_run_id, effect_type, entity_id, payload_hash"
                        ") VALUES ("
                        ":effect_id, :job_run_id, 'SIGNAL', :signal_id, :payload_hash"
                        ")"
                    ),
                    {
                        "effect_id": effect.effect_id,
                        "job_run_id": run.job_run_id,
                        "signal_id": signal.signal_id,
                        "payload_hash": effect.payload_hash,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO signals("
                        "signal_id, instrument_id, decision_time, state, "
                        "strategy_version"
                        ") VALUES ("
                        ":signal_id, :instrument_id, :decision_time, 'SIGNAL', "
                        "'partial-v1'"
                        ")"
                    ),
                    {
                        "signal_id": signal.signal_id,
                        "instrument_id": signal.instrument_id,
                        "decision_time": signal.decision_time,
                    },
                )

    engine.dispose()
