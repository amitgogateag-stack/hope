from datetime import datetime, timezone

import pytest

from hope.application.jobs import create_scheduled_job_run
from hope.infrastructure.paper_runtime import (
    PaperJobDefinition,
    PaperJobRegistry,
    SqlAlchemyPaperRuntime,
    run_paper_once,
)


UTC = timezone.utc


def test_paper_job_definition_requires_canonical_key_and_callable_handler() -> None:
    with pytest.raises(ValueError, match="PAPER_JOB_KEY_REQUIRED"):
        PaperJobDefinition("", lambda runtime: None)
    with pytest.raises(ValueError, match="PAPER_JOB_KEY_NOT_CANONICAL"):
        PaperJobDefinition(" job ", lambda runtime: None)
    with pytest.raises(TypeError, match="PAPER_JOB_HANDLER_MUST_BE_CALLABLE"):
        PaperJobDefinition("job", object())


def test_paper_job_registry_requires_typed_definitions_and_rejects_duplicates() -> None:
    handler = lambda runtime: None
    with pytest.raises(TypeError, match="PAPER_JOB_DEFINITION_REQUIRED"):
        PaperJobRegistry([("job", handler)])
    with pytest.raises(ValueError, match="PAPER_JOB_KEY_DUPLICATE"):
        PaperJobRegistry(
            [
                PaperJobDefinition("job", handler),
                PaperJobDefinition("job", handler),
            ]
        )


def test_paper_job_registry_rejects_unregistered_job() -> None:
    job_run = create_scheduled_job_run("unknown", datetime(2026, 9, 10, 14, 0, tzinfo=UTC))
    with pytest.raises(RuntimeError, match="PAPER_JOB_NOT_REGISTERED"):
        PaperJobRegistry([]).resolve(job_run)


def test_concrete_paper_runtime_has_no_public_arbitrary_work_entrypoint() -> None:
    assert not hasattr(SqlAlchemyPaperRuntime, "run")


def test_run_paper_once_rejects_raw_callback_before_engine_use() -> None:
    job_run = create_scheduled_job_run("job", datetime(2026, 9, 10, 14, 1, tzinfo=UTC))
    with pytest.raises(TypeError, match="PAPER_ONE_SHOT_REQUIRES_JOB_REGISTRY"):
        run_paper_once(
            object(),
            job_run,
            lambda runtime: None,
            now=lambda: job_run.scheduled_for,
        )
