from datetime import datetime, timedelta, timezone

import pytest

from hope.application.jobs import (
    JobRunStatus,
    create_job_run_completion,
    create_scheduled_job_run,
)


UTC = timezone.utc


def _job_run():
    return create_scheduled_job_run(
        "paper-cycle",
        datetime(2026, 9, 9, 18, 0, tzinfo=UTC),
    )


def test_success_completion_normalizes_time_to_utc():
    run = _job_run()
    completion_time = datetime(2026, 9, 9, 14, 5, tzinfo=timezone(timedelta(hours=-4)))

    completion = create_job_run_completion(
        run,
        JobRunStatus.SUCCEEDED,
        completion_time,
    )

    assert completion.status is JobRunStatus.SUCCEEDED
    assert completion.completed_at == datetime(2026, 9, 9, 18, 5, tzinfo=UTC)
    assert completion.failure_code is None


def test_failed_completion_requires_nonblank_failure_code():
    run = _job_run()

    with pytest.raises(ValueError, match="JOB_FAILURE_CODE_REQUIRED"):
        create_job_run_completion(
            run,
            JobRunStatus.FAILED,
            datetime(2026, 9, 9, 18, 5, tzinfo=UTC),
            failure_code="   ",
        )


def test_failed_completion_normalizes_failure_code():
    run = _job_run()

    completion = create_job_run_completion(
        run,
        JobRunStatus.FAILED,
        datetime(2026, 9, 9, 18, 5, tzinfo=UTC),
        failure_code="  UPSTREAM_DATA_UNAVAILABLE  ",
    )

    assert completion.failure_code == "UPSTREAM_DATA_UNAVAILABLE"


def test_success_completion_rejects_failure_code():
    run = _job_run()

    with pytest.raises(ValueError, match="JOB_SUCCESS_CANNOT_HAVE_FAILURE_CODE"):
        create_job_run_completion(
            run,
            JobRunStatus.SUCCEEDED,
            datetime(2026, 9, 9, 18, 5, tzinfo=UTC),
            failure_code="UNEXPECTED",
        )


def test_job_completion_rejects_naive_time():
    run = _job_run()

    with pytest.raises(ValueError, match="JOB_COMPLETION_TIME_MUST_BE_TIMEZONE_AWARE"):
        create_job_run_completion(
            run,
            JobRunStatus.SUCCEEDED,
            datetime(2026, 9, 9, 18, 5),
        )


def test_job_completion_rejects_time_before_schedule():
    run = _job_run()

    with pytest.raises(ValueError, match="JOB_COMPLETION_PRECEDES_SCHEDULE"):
        create_job_run_completion(
            run,
            JobRunStatus.SUCCEEDED,
            datetime(2026, 9, 9, 17, 59, tzinfo=UTC),
        )


def test_job_completion_rejects_claimed_status():
    run = _job_run()

    with pytest.raises(ValueError, match="JOB_COMPLETION_STATUS_REQUIRED"):
        create_job_run_completion(
            run,
            JobRunStatus.CLAIMED,
            datetime(2026, 9, 9, 18, 5, tzinfo=UTC),
        )
