import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from hope.application.jobs import JobRunStatus, create_scheduled_job_run
from hope.domain.execution.models import Environment, ExecutionRejection, OrderSide
from hope.domain.execution.simulator import Fill
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.paper_runtime import PaperJobDefinition, PaperJobRegistry, run_paper_once
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository


UTC = timezone.utc


def _engine():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    return create_engine(url)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("effect_kind", "error"),
    (
        ("signal", "PAPER_SIGNAL_DECISION_AFTER_JOB_SCHEDULE"),
        ("fill", "PAPER_FILL_AFTER_JOB_SCHEDULE"),
        ("terminal", "PAPER_TERMINAL_AFTER_JOB_SCHEDULE"),
    ),
)
def test_direct_runtime_effect_cannot_bypass_job_schedule(
    effect_kind: str,
    error: str,
) -> None:
    engine = _engine()
    job_run = create_scheduled_job_run(
        f"paper-direct-future-{effect_kind}-{uuid4()}",
        datetime(2026, 10, 14, 15, 30, tzinfo=UTC),
    )
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)

    def write_future_effect(runtime) -> None:
        signal_id = uuid4()
        instrument_id = uuid4()
        future_time = job_run.scheduled_for + timedelta(microseconds=1)
        if effect_kind == "signal":
            signal = Signal(
                signal_id=runtime.cycle.signal_id(
                    instrument_id=instrument_id,
                    strategy_version="direct-runtime-v1",
                    decision_time=future_time,
                    signal_type=SignalType.ENTRY,
                    conviction=Decimal("0.75"),
                    inputs_hash="a" * 64,
                ),
                instrument_id=instrument_id,
                strategy_version="direct-runtime-v1",
                decision_time=future_time,
                signal_type=SignalType.ENTRY,
                conviction=Decimal("0.75"),
                inputs_hash="a" * 64,
            )
            runtime.record_signal(signal)
            return

        if effect_kind == "terminal":
            runtime.record_terminal(
                ExecutionRejection(
                    order_id=runtime.cycle.order_id(signal_id),
                    signal_id=signal_id,
                    instrument_id=instrument_id,
                    environment=Environment.PAPER,
                    reason_code="DIRECT_RUNTIME_REJECT",
                    rejection_time=future_time,
                )
            )
            return

        runtime.record_fill(
            uuid4(),
            Decimal("1000"),
            Fill(
                fill_id=runtime.cycle.fill_id(signal_id, 0),
                order_id=runtime.cycle.order_id(signal_id),
                signal_id=signal_id,
                instrument_id=instrument_id,
                side=OrderSide.BUY,
                quantity=Decimal("1"),
                price=Decimal("100"),
                commission=Decimal("0"),
                slippage=Decimal("0"),
                cost_model_version="direct-runtime-v1",
                fill_time=future_time,
            ),
            sequence=0,
        )

    registry = PaperJobRegistry(
        [PaperJobDefinition(job_run.job_key, write_future_effect)]
    )
    with pytest.raises(ValueError, match=error):
        run_paper_once(
            engine,
            job_run,
            registry,
            now=lambda: job_run.scheduled_for + timedelta(minutes=1),
        )

    with engine.connect() as connection:
        record = SqlAlchemyJobRunRepository(connection).get_record(job_run.job_run_id)
        assert record is not None
        assert record.status is JobRunStatus.FAILED
        assert connection.execute(
            text("SELECT count(*) FROM paper_effects WHERE job_run_id=:job_run_id"),
            {"job_run_id": job_run.job_run_id},
        ).scalar_one() == 0
    engine.dispose()
