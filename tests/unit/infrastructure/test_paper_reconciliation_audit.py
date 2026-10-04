from uuid import uuid4

import pytest

from hope.infrastructure.repositories.paper_reconciliation_audit import (
    SqlAlchemyPaperReconciliationAuditRepository,
)


class _InsertedResult:
    def __init__(self, inserted_id):
        self._inserted_id = inserted_id

    def scalar_one_or_none(self):
        return self._inserted_id


class _Connection:
    def execute(self, statement):
        return _InsertedResult(uuid4())


def test_new_reconciliation_receipt_must_be_reread_before_record_succeeds(
    monkeypatch,
) -> None:
    repository = SqlAlchemyPaperReconciliationAuditRepository(_Connection())
    monkeypatch.setattr(
        repository,
        "_expected",
        lambda completion, effects: (uuid4(), "job-run", {}),
    )
    verify_calls = []

    def _not_durable(completion, effects):
        verify_calls.append((completion, effects))
        return False

    monkeypatch.setattr(repository, "verify", _not_durable)
    completion = object()
    effects = ()

    with pytest.raises(
        RuntimeError,
        match="PAPER_RECONCILIATION_AUDIT_NOT_DURABLE",
    ):
        repository.record(completion, effects)

    assert verify_calls == [(completion, effects)]

class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _ReadConnection:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, statement):
        return _RowsResult(self._rows)


def test_verify_rejects_same_job_receipt_with_different_audit_identity(
    monkeypatch,
) -> None:
    expected_id = uuid4()
    conflicting_id = uuid4()
    repository = SqlAlchemyPaperReconciliationAuditRepository(
        _ReadConnection(
            [
                {
                    "audit_event_id": conflicting_id,
                    "event_type": "PAPER_RUN_RECONCILED",
                    "entity_type": "JOB_RUN",
                    "entity_id": "job-run",
                    "payload": {"schema_version": 1},
                }
            ]
        )
    )
    monkeypatch.setattr(
        repository,
        "_expected",
        lambda completion, effects: (
            expected_id,
            "job-run",
            {"schema_version": 1},
        ),
    )

    with pytest.raises(
        ValueError,
        match="PAPER_RECONCILIATION_AUDIT_IDENTITY_CONFLICT",
    ):
        repository.verify(object(), ())

