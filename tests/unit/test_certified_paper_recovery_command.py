from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from hope.api import paper_recovery as cli
from hope.infrastructure.scheduling.certified_paper_recovery import recover_certified_paper_batch


@pytest.mark.parametrize("selection", [(), ("bad",), (uuid4(),)*2, (uuid4(),)*33])
def test_invalid_selection_never_touches_database(selection):
    with pytest.raises(ValueError, match="SELECTION"):
        recover_certified_paper_batch(None, selection, current=datetime.now(UTC))


def test_unbounded_input_stops_consumption_at_33():
    consumed=[]
    def values():
        while True:
            consumed.append(True)
            yield uuid4()
    with pytest.raises(ValueError, match="BOUNDS"):
        recover_certified_paper_batch(None, values(), current=datetime.now(UTC))
    assert len(consumed) == 33


@pytest.mark.parametrize("clock", [None, "now", datetime(2026,1,1)])
def test_invalid_clock_never_touches_database(clock):
    with pytest.raises(ValueError, match="CLOCK"):
        recover_certified_paper_batch(None, (uuid4(),), current=clock)


def test_cli_requires_configuration_without_echoing_environment(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("HOPE_DATABASE_URL",raising=False)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--run-id",str(uuid4()),"--migrations-dir",str(tmp_path)])
    assert exc.value.code == 2
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("bad", ["invalid", str(uuid4()).upper(), uuid4().hex])
def test_cli_requires_explicit_canonical_identity(tmp_path,bad):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--run-id",bad,"--migrations-dir",str(tmp_path)])
    assert exc.value.code == 2


@pytest.mark.parametrize("failure", ["recovery","commit"])
def test_cli_never_publishes_success_before_commit_ack(tmp_path, monkeypatch, capsys, failure):
    disposed=[]
    @contextmanager
    def begin():
        yield object()
        if failure == "commit":
            raise RuntimeError("secret commit acknowledgement")
    engine=SimpleNamespace(begin=begin,dispose=lambda:disposed.append(True))
    monkeypatch.setenv("HOPE_DATABASE_URL","secret database configuration")
    monkeypatch.setattr(cli,"create_engine",lambda *a,**k:engine)
    monkeypatch.setattr(cli,"verify_schema_compatibility",lambda *a:None)
    def recover(*a,**k):
        if failure == "recovery":
            raise RuntimeError("secret database payload")
        return SimpleNamespace(reconciled_run_ids=(),authenticated_run_ids=(uuid4(),))
    monkeypatch.setattr(cli,"recover_certified_paper_batch",recover)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--run-id",str(uuid4()),"--migrations-dir",str(tmp_path)])
    output=capsys.readouterr()
    assert exc.value.code == 1 and output.out == ""
    assert output.err == "PAPER_CERTIFIED_RECOVERY_UNAVAILABLE\n" and disposed == [True]
