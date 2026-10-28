"""Release environments survive switches, failed activation and recovery."""
import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from hope.api import dashboard_activation as m
from test_dashboard_only_activation import OLD,NEW,THIRD,setup
from test_activation_guard import guard_platform


def switch(layout,host,target=NEW,runtime="release"):
    return m.activate(layout,host,target=target,shared=OLD,ci_run=123,ci_attempt=1,runtime=runtime)


def test_release_runtime_is_explicit_durable_and_restores_prior_environment(setup):
    layout,host=setup
    switch(layout,host)
    first=layout.journal.read_bytes()
    record=m._record(layout)
    assert record["schema"]=="hope.dashboard-activation.v2"
    assert record["runtime"]=="release" and record["previous_runtime"]=="shared"
    python=layout.root/"release-venvs"/NEW/"bin/python"
    assert m._python(layout,NEW)==python
    for config in m._configs(layout,NEW).values():
        assert f"{python} -B -s -P" in config
        assert str(layout.python) not in config
        assert "UnsetEnvironment=" in config
    switch(layout,host,THIRD,"shared")
    assert m._record(layout)["previous_runtime"]=="release"
    m.rollback(layout,host,target=THIRD,shared=OLD)
    assert layout.journal.read_bytes()==first
    assert m._python(layout,NEW)==python
    m.rollback(layout,host,target=NEW,shared=OLD)
    assert m._record(layout) is None and m._python(layout,OLD)==layout.python


@pytest.mark.parametrize("failure",["stop","reload","start","verify","paper_safe"])
def test_release_failure_restores_shared_and_preserves_paper_pointer(setup,failure):
    layout,host=setup;host.failure=failure
    with pytest.raises(RuntimeError):switch(layout,host)
    assert m._record(layout) is None
    assert m._pointer(layout,"current")==OLD
    assert m._pointer(layout,"dashboard-current",optional=True) is None


def test_release_request_gate_rejects_shared_interpreter_even_with_same_base_python(setup,monkeypatch):
    layout,host=setup;switch(layout,host)
    monkeypatch.setattr(m.sys,"executable",str(layout.python))
    with pytest.raises(RuntimeError,match="RUNTIME_IDENTITY_MISMATCH"):m.runtime_gate(layout,NEW)
    python=m._python(layout,NEW)
    monkeypatch.setattr(m.sys,"executable",str(python))
    monkeypatch.setattr(m.sys,"prefix",str(python.parent.parent))
    assert m.runtime_gate(layout,NEW)["execution_authorized"] is False


def test_selected_missing_environment_never_falls_back_to_shared(setup,monkeypatch):
    layout,_=setup;host=m.Host(layout);host.target_runtime="release"
    layout.python.parent.mkdir(parents=True);layout.python.write_text("shared")
    host.run=Mock()
    with pytest.raises(FileNotFoundError):host.runtime(layout.root/"releases"/NEW,new=True)
    host.run.assert_not_called()


def test_release_runtime_probe_uses_selected_prefix_and_sanitized_environment(setup,monkeypatch):
    layout,_=setup;host=m.Host(layout);host.target_runtime="release"
    python=m._python(layout,NEW,"release");python.parent.mkdir(parents=True);python.write_text("python")
    host.run=Mock(return_value="DASHBOARD_RUNTIME_VERIFIED\n")
    host.runtime(layout.root/"releases"/NEW,new=True)
    command=host.run.call_args.args
    assert command[4]==str(python)
    assert "sys.prefix" in command[-1] and "sys.executable" in command[-1]
    assert "PYTHONHOME" not in host.run.call_args.kwargs["env"]


def test_rollback_to_legacy_shared_release_presents_v1_journal_before_start(setup):
    layout,host=setup;switch(layout,host,runtime="shared")
    prior=layout.journal.read_bytes();switch(layout,host,THIRD)
    def start(timer):
        record=m._record(layout)
        assert record["schema"]=="hope.dashboard-activation.v1"
        assert record["phase"]=="ROLLING_BACK"
        assert "runtime" not in record
    host.start=start
    m.rollback(layout,host,target=THIRD,shared=OLD)
    assert layout.journal.read_bytes()==prior


@pytest.mark.skipif(os.name!="posix",reason="POSIX trust metadata; Linux CI")
@pytest.mark.parametrize("unsafe",["parent_link","environment_link","writable","python_link_owner"])
def test_release_runtime_refuses_untrusted_storage_before_imports(setup,monkeypatch,unsafe):
    layout,_=setup;host=m.Host(layout);host.target_runtime="release"
    python=m._python(layout,NEW,"release");python.parent.mkdir(parents=True);python.write_text("python")
    if unsafe=="writable":python.parent.parent.chmod(0o777)
    elif unsafe=="environment_link":
        env=python.parent.parent;env.rename(env.with_name("substitute"));env.symlink_to(env.with_name("substitute"),target_is_directory=True)
    elif unsafe=="parent_link":
        env=layout.root/"release-venvs";env.rename(layout.root/"other");env.symlink_to(layout.root/"other",target_is_directory=True)
    else:
        python.unlink();python.symlink_to(Path(__file__))
        original=Path.lstat
        from types import SimpleNamespace
        def changed(path,*a,**k):
            if path==python:return SimpleNamespace(st_uid=m.OWNER+1,st_mode=0o120777,st_nlink=1)
            return original(path,*a,**k)
        monkeypatch.setattr(Path,"lstat",changed)
    host.run=Mock()
    with pytest.raises((RuntimeError,OSError)):host.runtime(layout.root/"releases"/NEW,new=True)
    host.run.assert_not_called()


@pytest.mark.parametrize("field,value",[("runtime","auto"),("previous_runtime","auto"),
    ("schema","hope.dashboard-activation.v1"),("previous_runtime","release")])
def test_runtime_record_tampering_is_blocked(setup,field,value):
    layout,host=setup;switch(layout,host)
    record=m._record(layout);record[field]=value
    m._save(layout.journal,record)
    with pytest.raises(RuntimeError,match="RECORD_INVALID"):m._record(layout)


def audit_result():
    return {"checks":{"canonical_unit":"ABSENT_REQUIRES_REVIEW",**{
        key:{"status":"REVIEW_REQUIRED","counts":[0]} for key in
        ("processes","systemd","schedules","deployment")}}}


ABSENT="LoadState=not-found\nActiveState=inactive\nUnitFileState=\nMainPID=0\nFragmentPath=\nDropInPaths=\n"


@pytest.mark.parametrize("failure",["approval","processes","systemd","schedules","deployment","canonical","existing","dropin","legacy"])
def test_paper_mask_blocks_before_mutation_without_complete_review(setup,monkeypatch,failure):
    layout,_=setup;host=m.Host(layout);host.reload=Mock();host.legacy_safe=Mock();host.paper_safe=Mock()
    host.paper_properties=Mock(return_value=ABSENT)
    result=audit_result()
    if failure in {"processes","systemd","schedules","deployment"}:result["checks"][failure]["status"]="UNVERIFIED"
    if failure=="canonical":result["checks"]["canonical_unit"]="UNVERIFIED"
    if failure=="existing":(layout.units/m.PAPER).write_text("existing")
    if failure=="dropin":(layout.units/(m.PAPER+".d")).mkdir()
    if failure=="legacy":host.legacy_safe.side_effect=RuntimeError("legacy")
    monkeypatch.setattr(m.paper_absence_audit,"audit",lambda host:result)
    with pytest.raises(RuntimeError):m.install_paper_mask(layout,host,reviewed=failure!="approval")
    host.reload.assert_not_called();host.paper_safe.assert_not_called()
    if failure!="existing":assert not (layout.units/m.PAPER).is_symlink()


@pytest.mark.skipif(os.name!="posix",reason="Native /dev/null mask; Linux CI")
@pytest.mark.parametrize("reload_fails",[False,True])
def test_paper_mask_only_creates_nonexecutable_mask_and_retains_it_on_failure(setup,monkeypatch,reload_fails):
    layout,_=setup;host=m.Host(layout);host.run=Mock();host.legacy_safe=Mock();host.paper_safe=Mock()
    host.paper_properties=Mock(side_effect=[ABSENT,
        "LoadState=masked\nActiveState=inactive\nUnitFileState=masked\nMainPID=0\nFragmentPath=/dev/null\nDropInPaths=\n"])
    host.reload=Mock(side_effect=RuntimeError("reload") if reload_fails else None)
    def audited(host):
        assert (layout.root/".activation.guard").exists()
        with m.paper_absence_audit.deployment_view(layout.root):pass
        return audit_result()
    monkeypatch.setattr(m.paper_absence_audit,"audit",audited)
    if reload_fails:
        with pytest.raises(RuntimeError,match="reload"):m.install_paper_mask(layout,host,reviewed=True)
    else:assert m.install_paper_mask(layout,host,reviewed=True)["activation_authorized"] is False
    assert os.readlink(layout.units/m.PAPER)=="/dev/null"
    host.run.assert_not_called()
    assert m._pointer(layout,"current")==OLD
    assert m._record(layout) is None
