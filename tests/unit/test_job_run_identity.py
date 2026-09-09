from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from hope.application.jobs import ScheduledJobRun, create_scheduled_job_run


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


def test_restored_scheduled_job_run_rejects_arbitrary_identity():
    valid = create_scheduled_job_run(
        "paper-cycle",
        datetime(2026, 9, 9, 18, 0, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="JOB_RUN_IDENTITY_MISMATCH"):
        ScheduledJobRun(
            job_run_id=uuid4(),
            job_key=valid.job_key,
            scheduled_for=valid.scheduled_for,
        )


def test_restored_scheduled_job_run_rejects_naive_schedule_time():
    valid = create_scheduled_job_run(
        "paper-cycle",
        datetime(2026, 9, 9, 18, 0, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="JOB_SCHEDULE_TIME_MUST_BE_TIMEZONE_AWARE"):
        ScheduledJobRun(
            job_run_id=valid.job_run_id,
            job_key=valid.job_key,
            scheduled_for=datetime(2026, 9, 9, 18, 0),
        )


def test_restored_scheduled_job_run_rejects_blank_job_key():
    valid = create_scheduled_job_run(
        "paper-cycle",
        datetime(2026, 9, 9, 18, 0, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="JOB_KEY_REQUIRED"):
        ScheduledJobRun(
            job_run_id=valid.job_run_id,
            job_key="   ",
            scheduled_for=valid.scheduled_for,
        )


def test_restored_scheduled_job_run_canonicalizes_key_and_time():
    valid = create_scheduled_job_run(
        "paper-cycle",
        datetime(2026, 9, 9, 18, 0, tzinfo=UTC),
    )
    offset_time = valid.scheduled_for.astimezone(timezone(timedelta(hours=-5)))

    restored = ScheduledJobRun(
        job_run_id=valid.job_run_id,
        job_key="  paper-cycle  ",
        scheduled_for=offset_time,
    )

    assert restored == valid
    assert restored.job_key == "paper-cycle"
    assert restored.scheduled_for == valid.scheduled_for
