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
