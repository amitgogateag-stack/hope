from datetime import UTC, datetime
from uuid import uuid4

import pytest

from hope.application.jobs import JobRunStatus, create_job_run_completion, create_scheduled_job_run
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository


@pytest.mark.parametrize("status,code", [(JobRunStatus.SUCCEEDED, None), (JobRunStatus.FAILED, 'OTHER_FAILURE')])
def test_quarantine_rejects_non_quarantine_completion_before_database_work(status, code):
    run = create_scheduled_job_run(f"paper-quarantine-{uuid4()}", datetime(2026, 10, 1, tzinfo=UTC))
    completion = create_job_run_completion(run, status, run.scheduled_for, failure_code=code)
    repository = SqlAlchemyJobRunRepository(None)
    with pytest.raises(ValueError, match="PAPER_JOB_QUARANTINE_COMPLETION_INVALID"):
        repository.quarantine_empty_claim(completion)
