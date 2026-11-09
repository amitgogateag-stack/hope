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


class _BrokenCleanupEngine(_Engine):
    def __init__(self):
        super().__init__(True);self.invalidations=[]
        self.connection.invalidate=lambda error:self.invalidations.append(error)

    @contextmanager
    def connect(self):
        try:yield self.connection
        finally:
            self.exited=True
            raise RuntimeError('SYNTHETIC_EXPIRED_SCHEDULER_CONNECTION')


def test_scheduler_cleanup_failure_cannot_replace_primary_financial_failure():
    engine=_BrokenCleanupEngine();primary=ValueError('SYNTHETIC_FINANCIAL_COMMIT_FAILURE')
    with pytest.raises(ValueError) as observed:
        with _operational_paper_scheduler_lock(engine):raise primary
    assert observed.value is primary and engine.invalidations==[primary] and engine.exited


def test_scheduler_cleanup_failure_without_primary_error_still_fails_closed():
    engine=_BrokenCleanupEngine()
    with pytest.raises(RuntimeError,match='SYNTHETIC_EXPIRED_SCHEDULER_CONNECTION'):
        with _operational_paper_scheduler_lock(engine):pass
    assert engine.invalidations==[] and engine.exited
