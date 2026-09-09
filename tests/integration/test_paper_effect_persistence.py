import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.application.jobs import (
    JobRunStatus,
    create_job_run_completion,
    create_scheduled_job_run,
)
from hope.application.paper import PaperEffectType, create_paper_effect
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository


UTC = timezone.utc
PAYLOAD = "b" * 64


@pytest.mark.integration
def test_paper_effects_are_durable_idempotent_and_require_claimed_job() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    first_run = create_scheduled_job_run(
        "paper-effect-first",
        datetime(2026, 9, 9, 22, 0, tzinfo=UTC),
    )
    second_run = create_scheduled_job_run(
        "paper-effect-second",
        datetime(2026, 9, 9, 22, 1, tzinfo=UTC),
    )
    terminal_run = create_scheduled_job_run(
        "paper-effect-terminal",
        datetime(2026, 9, 9, 22, 2, tzinfo=UTC),
    )
    job_keys = (first_run.job_key, second_run.job_key, terminal_run.job_key)

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        cleanup_effects = text(
            "DELETE FROM paper_effects WHERE job_run_id IN ("
            "SELECT job_run_id FROM job_runs WHERE job_key IN :job_keys)"
        ).bindparams(bindparam("job_keys", expanding=True))
        cleanup_jobs = text(
            "DELETE FROM job_runs WHERE job_key IN :job_keys"
        ).bindparams(bindparam("job_keys", expanding=True))
        connection.execute(cleanup_effects, {"job_keys": job_keys})
        connection.execute(cleanup_jobs, {"job_keys": job_keys})

        jobs = SqlAlchemyJobRunRepository(connection)
        effects = SqlAlchemyPaperEffectRepository(connection)
        assert jobs.claim(first_run) is True
        assert jobs.claim(second_run) is True
        assert jobs.claim(terminal_run) is True

        entity_id = uuid4()
        first_effect = create_paper_effect(
            first_run,
            PaperEffectType.SIGNAL,
            entity_id,
            PAYLOAD,
        )
        repeated_effect = create_paper_effect(
            second_run,
            PaperEffectType.SIGNAL,
            entity_id,
            PAYLOAD,
        )

        assert effects.record(first_effect) is True
        assert effects.record(repeated_effect) is False

        stored = effects.get(PaperEffectType.SIGNAL, entity_id)
        assert stored is not None
        assert stored.effect_id == first_effect.effect_id
        assert stored.job_run_id == first_run.job_run_id
        assert stored.payload_hash == PAYLOAD

        conflicting_effect = create_paper_effect(
            second_run,
            PaperEffectType.SIGNAL,
            entity_id,
            "c" * 64,
        )
        with pytest.raises(ValueError, match="PAPER_EFFECT_IDENTITY_CONFLICT"):
            effects.record(conflicting_effect)

        completion = create_job_run_completion(
            terminal_run,
            JobRunStatus.SUCCEEDED,
            terminal_run.scheduled_for + timedelta(minutes=1),
        )
        assert jobs.complete(completion) is True

        terminal_effect = create_paper_effect(
            terminal_run,
            PaperEffectType.ORDER,
            uuid4(),
            PAYLOAD,
        )
        with pytest.raises(IntegrityError) as exc_info:
            with connection.begin_nested():
                effects.record(terminal_effect)
        assert "PAPER_EFFECT_REQUIRES_CLAIMED_JOB" in str(exc_info.value)
