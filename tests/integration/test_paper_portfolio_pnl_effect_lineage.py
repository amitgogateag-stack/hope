import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.application.jobs import (
    JobRunStatus,
    create_job_run_completion,
    create_scheduled_job_run,
)
from hope.application.paper.effects import PaperEffectType, create_paper_effect
from hope.application.paper.portfolio_pnl import (
    PaperPortfolioPnLEvent,
    paper_portfolio_pnl_event_id,
    paper_portfolio_pnl_payload_hash,
)
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository


@pytest.mark.integration
def test_paper_portfolio_pnl_requires_canonical_compatible_effect_lineage() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    event_time = datetime(2026, 10, 8, 8, 30, tzinfo=timezone.utc)
    source_run = create_scheduled_job_run(
        "paper-portfolio-pnl-source-lineage",
        event_time - timedelta(minutes=2),
    )
    pnl_run = create_scheduled_job_run(
        "paper-portfolio-pnl-effect-lineage",
        event_time - timedelta(minutes=1),
    )
    instrument_id = uuid4()
    signal_id = uuid4()
    order_id = uuid4()
    fill_id = uuid4()
    portfolio_id = uuid4()
    event_id = paper_portfolio_pnl_event_id(portfolio_id, fill_id)
    event = PaperPortfolioPnLEvent(
        pnl_event_id=event_id,
        portfolio_id=portfolio_id,
        fill_id=fill_id,
        instrument_id=instrument_id,
        realized_pnl_delta=Decimal("0"),
        commission_delta=Decimal("0.25"),
        event_time=event_time,
    )
    insert_event = text(
        "INSERT INTO paper_portfolio_pnl_events("
        "pnl_event_id, portfolio_id, fill_id, instrument_id, "
        "realized_pnl_delta, commission_delta, event_time"
        ") VALUES ("
        ":event_id, :portfolio_id, :fill_id, :instrument_id, 0, 0.25, :event_time"
        ")"
    )
    event_params = {
        "event_id": event_id,
        "portfolio_id": portfolio_id,
        "fill_id": fill_id,
        "instrument_id": instrument_id,
        "event_time": event_time,
    }

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            apply_migrations(connection, migrations_dir)
            connection.execute(
                text(
                    "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                    "VALUES (:id, 'PNL-EFFECT-LINEAGE', 'TEST', 'ACTIVE')"
                ),
                {"id": instrument_id},
            )
            connection.execute(
                text(
                    "INSERT INTO signals(signal_id, instrument_id, decision_time, state) "
                    "VALUES (:id, :instrument_id, :event_time, 'SIGNAL')"
                ),
                {"id": signal_id, "instrument_id": instrument_id, "event_time": event_time},
            )
            connection.execute(
                text(
                    "INSERT INTO orders(order_id, signal_id, instrument_id, environment, side, quantity) "
                    "VALUES (:id, :signal_id, :instrument_id, 'PAPER', 'BUY', 1)"
                ),
                {"id": order_id, "signal_id": signal_id, "instrument_id": instrument_id},
            )
            connection.execute(
                text(
                    "INSERT INTO fills(fill_id, order_id, quantity, fill_price, slippage, "
                    "transaction_cost, filled_at, cost_model_version) "
                    "VALUES (:id, :order_id, 1, 100, 0, 0.25, :event_time, 'test-cost-v1')"
                ),
                {"id": fill_id, "order_id": order_id, "event_time": event_time},
            )
            connection.execute(
                text(
                    "INSERT INTO paper_portfolios(portfolio_id, initial_cash, cash) "
                    "VALUES (:id, 1000, 900)"
                ),
                {"id": portfolio_id},
            )

            jobs = SqlAlchemyJobRunRepository(connection)
            assert jobs.claim(source_run) is True
            assert jobs.claim(pnl_run) is True
            effects = SqlAlchemyPaperEffectRepository(connection)
            connection.execute(
                text(
                    "INSERT INTO paper_portfolio_fill_applications("
                    "portfolio_id, fill_id, application_sequence"
                    ") VALUES (:portfolio_id, :fill_id, 1)"
                ),
                {"portfolio_id": portfolio_id, "fill_id": fill_id},
            )

            with pytest.raises(
                IntegrityError,
                match="PAPER_PORTFOLIO_PNL_EFFECT_UNTRACKED",
            ):
                with connection.begin_nested():
                    connection.execute(insert_event, event_params)
                    effects.record(
                        create_paper_effect(
                            source_run,
                            PaperEffectType.FILL,
                            fill_id,
                            "9" * 64,
                        )
                    )

            with pytest.raises(
                IntegrityError,
                match="PAPER_PORTFOLIO_PNL_EFFECT_OWNER_CONFLICT",
            ):
                with connection.begin_nested():
                    connection.execute(insert_event, event_params)
                    effects.record(
                        create_paper_effect(
                            pnl_run,
                            PaperEffectType.PNL,
                            event_id,
                            paper_portfolio_pnl_payload_hash(event),
                        )
                    )
                    effects.record(
                        create_paper_effect(
                            source_run,
                            PaperEffectType.FILL,
                            fill_id,
                            "8" * 64,
                        )
                    )

            savepoint = connection.begin_nested()
            connection.execute(insert_event, event_params)
            assert effects.record(
                create_paper_effect(
                    source_run,
                    PaperEffectType.PNL,
                    event_id,
                    paper_portfolio_pnl_payload_hash(event),
                )
            ) is True
            assert effects.record(
                create_paper_effect(
                    source_run,
                    PaperEffectType.FILL,
                    fill_id,
                    "7" * 64,
                )
            ) is True
            savepoint.rollback()

            with pytest.raises(
                IntegrityError,
                match="PAPER_PORTFOLIO_PNL_EFFECT_PAYLOAD_MISMATCH",
            ):
                with connection.begin_nested():
                    connection.execute(insert_event, event_params)
                    effects.record(
                        create_paper_effect(
                            source_run,
                            PaperEffectType.PNL,
                            event_id,
                            "0" * 64,
                        )
                    )

            assert effects.record(
                create_paper_effect(source_run, PaperEffectType.FILL, fill_id, "a" * 64)
            ) is True

            with pytest.raises(
                IntegrityError,
                match="PAPER_PORTFOLIO_PNL_EFFECT_UNTRACKED",
            ):
                with connection.begin_nested():
                    connection.execute(insert_event, event_params)

            with pytest.raises(
                IntegrityError,
                match="PAPER_PORTFOLIO_PNL_EFFECT_PAYLOAD_MISMATCH",
            ):
                with connection.begin_nested():
                    assert effects.record(
                        create_paper_effect(
                            source_run,
                            PaperEffectType.PNL,
                            event_id,
                            "0" * 64,
                        )
                    ) is True
                    connection.execute(insert_event, event_params)

            with pytest.raises(
                IntegrityError,
                match="PAPER_PNL_EFFECT_IDENTITY_MISMATCH",
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO paper_effects("
                            "effect_id, job_run_id, effect_type, entity_id, payload_hash"
                            ") VALUES (:effect_id, :job_run_id, 'PNL', :entity_id, :payload_hash)"
                        ),
                        {
                            "effect_id": uuid4(),
                            "job_run_id": pnl_run.job_run_id,
                            "entity_id": event_id,
                            "payload_hash": paper_portfolio_pnl_payload_hash(event),
                        },
                    )

            # Late PNL-effect insertion must not borrow a FILL whose owner
            # is still CLAIMED, even when the accounting row already exists.
            with pytest.raises(
                IntegrityError,
                match="PAPER_PORTFOLIO_PNL_EFFECT_OWNER_CONFLICT",
            ):
                with connection.begin_nested():
                    connection.execute(insert_event, event_params)
                    effects.record(
                        create_paper_effect(
                            pnl_run,
                            PaperEffectType.PNL,
                            event_id,
                            paper_portfolio_pnl_payload_hash(event),
                        )
                    )

            assert effects.record(
                create_paper_effect(
                    pnl_run,
                    PaperEffectType.PNL,
                    event_id,
                    paper_portfolio_pnl_payload_hash(event),
                )
            ) is True
            with pytest.raises(
                IntegrityError,
                match="PAPER_PORTFOLIO_PNL_EFFECT_OWNER_CONFLICT",
            ):
                with connection.begin_nested():
                    connection.execute(insert_event, event_params)

            assert jobs.complete(
                create_job_run_completion(
                    source_run,
                    JobRunStatus.SUCCEEDED,
                    event_time + timedelta(minutes=1),
                )
            ) is True
            connection.execute(insert_event, event_params)
            assert connection.execute(
                text(
                    "SELECT count(*) FROM paper_portfolio_pnl_events "
                    "WHERE pnl_event_id=:event_id"
                ),
                {"event_id": event_id},
            ).scalar_one() == 1
        finally:
            transaction.rollback()
