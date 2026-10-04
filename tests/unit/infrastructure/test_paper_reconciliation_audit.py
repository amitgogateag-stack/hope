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
