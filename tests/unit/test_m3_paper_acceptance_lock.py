from uuid import uuid4

import pytest

from hope.infrastructure.paper_acceptance import (
    _ACCEPTANCE_LOCK_NAMESPACE,
    _controlled_acceptance_lock,
)


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _Connection:
    def __init__(self, acquired):
        self.acquired = acquired
        self.calls = []
        self.closed = False

    def execute(self, statement, parameters=None):
        self.calls.append((str(statement), parameters))
        return _Result(self.acquired)

    def close(self):
        self.closed = True


class _Engine:
    def __init__(self, acquired):
        self.connection = _Connection(acquired)

    def connect(self):
        return self.connection


def test_acceptance_lock_is_read_only_transaction_scoped_and_run_specific():
    run_id = uuid4()
    engine = _Engine(True)

    connection = _controlled_acceptance_lock(engine,run_id)

    assert connection is engine.connection
    assert engine.connection.closed is False
    assert engine.connection.calls == [
        ("SET TRANSACTION READ ONLY",None),
        (
            "SELECT pg_try_advisory_xact_lock(hashtext(:lock_name)::bigint)",
            {"lock_name":f"{_ACCEPTANCE_LOCK_NAMESPACE}:{run_id}"},
        ),
    ]


def test_acceptance_lock_contention_closes_guard_connection():
    engine = _Engine(False)

    with pytest.raises(RuntimeError,match="^M3_ACCEPTANCE_CONCURRENT_RUN$"):
        _controlled_acceptance_lock(engine,uuid4())

    assert engine.connection.closed is True
