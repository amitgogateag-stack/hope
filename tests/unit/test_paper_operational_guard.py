"""Production CLI denial precedes factory code; native trust is independently read."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hope.api import paper_operational_guard as gate,paper_service as cli,runtime_identity
from hope.api import dashboard_activation as activation
from test_package_provenance import signed_runtime,SHA


def test_cli_denies_unverified_runtime_before_factory_import(tmp_path,monkeypatch,capsys):
    monkeypatch.setenv("HOPE_DATABASE_URL","test-only-not-a-credential")
    monkeypatch.delenv("HOPE_DEPLOYED_COMMIT",raising=False)
    factory=Mock();engine=Mock();monkeypatch.setattr(cli,"load_plan_factory",factory);monkeypatch.setattr(cli,"create_engine",engine)
    failure=Mock();monkeypatch.setattr(cli,"publish_startup_failure",failure)
    with pytest.raises(SystemExit) as error:
        cli.main(["--factory","untrusted:factory","--migrations-dir",str(tmp_path),"--status-file",str(tmp_path/"health")])
    assert error.value.code==2 and capsys.readouterr().err=="PAPER_SERVICE_STARTUP_FAILED\n"
    factory.assert_not_called();engine.assert_not_called()
    failure.assert_called_once()


@pytest.fixture
def guarded(signed_runtime,monkeypatch):
    root,env,mode,manifest,path=signed_runtime
    monkeypatch.setattr(activation,"OWNER",os.geteuid())
    monkeypatch.setenv("HOPE_DEPLOYED_COMMIT",SHA);monkeypatch.setenv("HOPE_LIVE_ENABLED","false")
    process=SimpleNamespace(prefix=str(env),executable=str(env/"bin/python"))
    monkeypatch.setattr(gate,"sys",process);monkeypatch.setattr(runtime_identity,"sys",process)
    monkeypatch.setattr(gate.operations,"inspect_release",Mock())
    (root/"current").symlink_to(root/"releases"/SHA,target_is_directory=True)
    return root,env,mode,path


def test_signed_package_lease_rechecks_before_work_and_preserves_worker_failures(guarded):
    root,env,mode,path=guarded
    with gate.paper_operational_guard(root/"releases"/SHA,root=root) as check:check();check()
    with pytest.raises(RuntimeError,match="PAPER_MARKET_DATA_STALE"):
        with gate.paper_operational_guard(root/"releases"/SHA,root=root):raise RuntimeError("PAPER_MARKET_DATA_STALE")


@pytest.mark.parametrize("fault",["live","signature","package","pointer","identity"])
def test_changed_trust_or_unsafe_environment_blocks_execution(guarded,monkeypatch,fault):
    root,env,mode,path=guarded
    if fault=="live":monkeypatch.setenv("HOPE_LIVE_ENABLED","true")
    elif fault=="signature":path.with_suffix(".sig").write_bytes(b"x"*64)
    elif fault=="package":next(env.rglob("__init__.py")).write_bytes(b"injected")
    elif fault=="pointer":(root/"current").unlink();(root/"current").symlink_to(root/"releases",target_is_directory=True)
    else:monkeypatch.setenv("HOPE_DEPLOYED_COMMIT","3"*40)
    ran=False
    with pytest.raises(RuntimeError,match="PAPER_RUNTIME_UNVERIFIED"):
        with gate.paper_operational_guard(root/"releases"/SHA,root=root):ran=True
    assert not ran


def test_restart_and_mid_session_replacement_require_new_proof(guarded):
    root,env,mode,path=guarded
    with pytest.raises(RuntimeError,match="PAPER_RUNTIME_UNVERIFIED"):
        with gate.paper_operational_guard(root/"releases"/SHA,root=root) as check:
            target=next(env.rglob("__init__.py"));target.write_bytes(b"changed")
            with pytest.raises(RuntimeError,match="PAPER_RUNTIME_UNVERIFIED"):check()
            # The closing check independently rejects a caught failure.
    with pytest.raises(RuntimeError,match="PAPER_RUNTIME_UNVERIFIED"):
        with gate.paper_operational_guard(root/"releases"/SHA,root=root):pass


def test_unverified_runtime_cycle_never_reports_current_or_notifies_ready(tmp_path,monkeypatch):
    from hope.infrastructure import paper_service as service
    cycle=Mock();monkeypatch.setattr(service,"run_service_cycle",cycle)
    @contextmanager
    def denied():raise RuntimeError("PAPER_RUNTIME_UNVERIFIED");yield
    # Status durability itself has native coverage; this test only models the
    # publish sink on Windows to isolate admission/control-flow from POSIX ACLs.
    published=[];monkeypatch.setattr(service,"publish_service_status",lambda path,value:published.append(dict(value)))
    if os.name!="posix":
        # Model an acquired native publication lease for this control-flow
        # test. Real multi-process exclusion and writes remain Linux tests.
        from hope.infrastructure import paper_history
        @contextmanager
        def acquired(path):
            yield SimpleNamespace(check=lambda:None,publish=lambda target,value,sink:sink(target,value))
        monkeypatch.setattr(paper_history,"publisher_lease",acquired)
    notifier=Mock()
    assert service.run_service(None,None,migrations_dir=tmp_path,status_file=tmp_path/"status",once=True,cycle_guard=denied,notifier=notifier)==1
    assert published[-1]["runtime_gate"]=="UNVERIFIED" and published[-1]["state"]=="UNAVAILABLE"
    assert published[-1]["error_code"]=="PAPER_RUNTIME_UNVERIFIED"
    cycle.assert_not_called();notifier.ready.assert_not_called()
