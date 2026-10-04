from contextlib import contextmanager

import pytest

from hope.infrastructure.scheduling.paper import (
    _PAPER_SCHEDULER_LOCK_NAME,
    _operational_paper_scheduler_lock,
)


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value


class _Connection:
    def __init__(self, acquired):
        self.acquired = acquired
        self.executions = []

    def execute(self, statement, parameters):
        self.executions.append((str(statement), parameters))
        return _ScalarResult(self.acquired)


class _Engine:
    def __init__(self, acquired):
        self.connection = _Connection(acquired)
        self.exited = False

    @contextmanager
    def connect(self):
        try:
            yield self.connection
        finally:
            self.exited = True


def test_scheduler_lock_uses_transaction_ownership_on_failure_exit() -> None:
    engine = _Engine(True)

    with pytest.raises(RuntimeError, match="EXPECTED_SCHEDULER_FAILURE"):
        with _operational_paper_scheduler_lock(engine):
            raise RuntimeError("EXPECTED_SCHEDULER_FAILURE")

    assert engine.exited is True
    assert len(engine.connection.executions) == 1
    statement, parameters = engine.connection.executions[0]
    assert "pg_try_advisory_xact_lock" in statement
    assert "pg_advisory_unlock" not in statement
    assert parameters == {"lock_name": _PAPER_SCHEDULER_LOCK_NAME}


def test_scheduler_transaction_lock_fails_closed_when_unavailable() -> None:
    engine = _Engine(False)
    entered = False

    with pytest.raises(RuntimeError, match="PAPER_SCHEDULER_CONCURRENT_RUN"):
        with _operational_paper_scheduler_lock(engine):
            entered = True

    assert entered is False
    assert engine.exited is True
    assert len(engine.connection.executions) == 1
