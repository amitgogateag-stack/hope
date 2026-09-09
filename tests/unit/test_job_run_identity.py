from datetime import datetime, timedelta, timezone

import pytest

from hope.application.jobs import create_scheduled_job_run


UTC = timezone.utc


def test_scheduled_job_run_identity_is_stable_for_same_instant_across_offsets():
    utc_time = datetime(2026, 9, 9, 18, 0, tzinfo=UTC)
    offset_time = utc_time.astimezone(timezone(timedelta(hours=-5)))

    first = create_scheduled_job_run("paper-cycle", utc_time)
    second = create_scheduled_job_run("paper-cycle", offset_time)

    assert first == second
    assert first.scheduled_for == utc_time


def test_scheduled_job_run_identity_changes_for_different_schedule():
    first = create_scheduled_job_run(
        "paper-cycle",
        datetime(2026, 9, 9, 18, 0, tzinfo=UTC),
    )
    second = create_scheduled_job_run(
        "paper-cycle",
        datetime(2026, 9, 9, 19, 0, tzinfo=UTC),
    )

    assert first.job_run_id != second.job_run_id


def test_scheduled_job_run_rejects_blank_job_key():
    with pytest.raises(ValueError, match="JOB_KEY_REQUIRED"):
        create_scheduled_job_run("   ", datetime(2026, 9, 9, 18, 0, tzinfo=UTC))


def test_scheduled_job_run_rejects_naive_schedule_time():
    with pytest.raises(ValueError, match="JOB_SCHEDULE_TIME_MUST_BE_TIMEZONE_AWARE"):
        create_scheduled_job_run("paper-cycle", datetime(2026, 9, 9, 18, 0))
