import pytest

from hope.infrastructure.repositories.paper_control import (
    SqlAlchemyPaperEnvironmentControlRepository,
)


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class _Connection:
    def __init__(self, state: object) -> None:
        self._state = state

    def execute(self, _statement: object) -> _ScalarResult:
        return _ScalarResult(self._state)


@pytest.mark.parametrize("state", ["PAUSED", " RUNNING ", "HALTED\t"])
def test_current_paper_environment_state_rejects_invalid_durable_value(
    state: str,
) -> None:
    repository = SqlAlchemyPaperEnvironmentControlRepository(  # type: ignore[arg-type]
        _Connection(state)
    )

    with pytest.raises(RuntimeError, match="PAPER_ENVIRONMENT_CONTROL_STATE_INVALID"):
        repository.current_state()


@pytest.mark.parametrize("state", ["RUNNING", "HALTED"])
def test_current_paper_environment_state_accepts_canonical_value(state: str) -> None:
    repository = SqlAlchemyPaperEnvironmentControlRepository(  # type: ignore[arg-type]
        _Connection(state)
    )

    assert repository.current_state() == state


class _RecordingConnection:
    def __init__(self) -> None:
        self.statements = []

    def execute(self, statement: object) -> _ScalarResult:
        self.statements.append(str(statement))
        return _ScalarResult(None)


def test_assert_running_holds_control_transition_lock(monkeypatch) -> None:
    connection = _RecordingConnection()
    repository = SqlAlchemyPaperEnvironmentControlRepository(  # type: ignore[arg-type]
        connection
    )
    monkeypatch.setattr(repository, "_assert_sequence_generator_ready", lambda: None)
    monkeypatch.setattr(repository, "current_state", lambda: "RUNNING")

    repository.assert_running()

    assert len(connection.statements) == 1
    assert "pg_advisory_xact_lock" in connection.statements[0]
    assert "hope:paper:environment-control" in connection.statements[0]
