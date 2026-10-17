from uuid import uuid4
from unittest.mock import Mock

import pytest

from hope.infrastructure.scheduling.paper_recovery_inspection import inspect_paper_runs


@pytest.mark.parametrize("selection", [(), ("invalid",), (None,), tuple(uuid4() for _ in range(33))])
def test_invalid_selection_never_touches_database(selection):
    connection = Mock()
    with pytest.raises(ValueError, match="PAPER_INSPECTION_SELECTION_"):
        inspect_paper_runs(connection, selection)
    connection.execute.assert_not_called()


def test_duplicate_selection_and_unbounded_generator_are_bounded():
    identity = uuid4()
    connection = Mock()
    with pytest.raises(ValueError, match="INVALID"):
        inspect_paper_runs(connection, (identity,identity))
    consumed = []
    def unlimited():
        while True:
            consumed.append(True)
            yield uuid4()
    with pytest.raises(ValueError, match="BOUNDS"):
        inspect_paper_runs(connection, unlimited())
    assert len(consumed) == 33
    connection.execute.assert_not_called()


@pytest.mark.parametrize("argument", ["INVALID", "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"])
def test_cli_requires_canonical_ids_before_database(monkeypatch, argument):
    from hope.api import paper_recovery_inspect as cli
    creator = Mock(side_effect=AssertionError("database touched"))
    monkeypatch.setattr(cli,"create_engine",creator)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--run-id",argument,"--migrations-dir","unused"])
    assert exc.value.code == 2
    creator.assert_not_called()


def test_cli_sanitizes_driver_errors(monkeypatch, capsys):
    from hope.api import paper_recovery_inspect as cli
    monkeypatch.setenv("HOPE_DATABASE_URL","unused-test-value")
    monkeypatch.setattr(cli,"create_engine",Mock(side_effect=RuntimeError("secret-driver-payload")))
    with pytest.raises(SystemExit) as exc:
        cli.main(["--run-id",str(uuid4()),"--migrations-dir","unused"])
    assert exc.value.code == 1
    assert "secret" not in str(exc.value) + capsys.readouterr().out


def test_cli_rejects_duplicates_and_missing_configuration_before_connecting(monkeypatch):
    from hope.api import paper_recovery_inspect as cli
    creator = Mock(side_effect=AssertionError("database touched"))
    monkeypatch.setattr(cli,"create_engine",creator)
    identity = str(uuid4())
    with pytest.raises(SystemExit) as exc:
        cli.main(["--run-id",identity,"--run-id",identity,"--migrations-dir","unused"])
    assert exc.value.code == 2
    monkeypatch.delenv("HOPE_DATABASE_URL",raising=False)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--run-id",identity,"--migrations-dir","unused"])
    assert exc.value.code == 2
    creator.assert_not_called()
