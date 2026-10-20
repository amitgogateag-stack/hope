"""Exporter absence is diagnostic evidence, never installation authority."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest

SOURCE = Path(__file__).parents[1] / "deploy/operations/inspect_exporter_install.py"
spec = importlib.util.spec_from_file_location("exporter_metadata_contract", SOURCE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
ABSENT = dict(LoadState="not-found",ActiveState="inactive",UnitFileState="",FragmentPath="",DropInPaths="")


@pytest.fixture(autouse=True)
def verify_temporary_unit_directory_as_runner(monkeypatch):
    """Exercise directory trust checks against the real test-runner owner."""
    original_open = mod._open_directory
    original_verify = mod._verify_directory_path
    monkeypatch.setattr(
        mod,
        "_open_directory",
        lambda path, *, owner_uid: original_open(path, owner_uid=os.getuid()),
    )
    monkeypatch.setattr(
        mod,
        "_verify_directory_path",
        lambda path, fd, *, owner_uid: original_verify(
            path, fd, owner_uid=os.getuid()
        ),
    )


def encoded(values):
    return "".join(f"{key}={value}\n" for key,value in values.items()).encode()


@pytest.mark.parametrize("status", [0,1,4])
def test_complete_canonical_absence_is_observable_even_when_systemctl_reports_missing(monkeypatch,status):
    monkeypatch.setattr(mod,"_bounded_capture",lambda *a,**k:subprocess.CompletedProcess([],status,encoded(ABSENT),b""))
    assert mod.unit_metadata(mod.NAMES[0]) == ABSENT


@pytest.mark.parametrize("status", [1,4,5,-9])
@pytest.mark.parametrize("change", [dict(LoadState="loaded"),dict(ActiveState="active"),
    dict(UnitFileState="enabled"),dict(FragmentPath="/run/other.service"),dict(DropInPaths="/run/override.conf")])
def test_nonzero_systemctl_cannot_authenticate_noncanonical_absence(monkeypatch,status,change):
    monkeypatch.setattr(mod,"_bounded_capture",lambda *a,**k:subprocess.CompletedProcess([],status,encoded(ABSENT|change),b""))
    with pytest.raises(ValueError,match="^UNIT_METADATA_UNVERIFIED$"):
        mod.unit_metadata(mod.NAMES[0])


@pytest.mark.parametrize("change", [dict(UnitFileState="enabled"),dict(UnitFileState="masked"),
    dict(UnitFileState="unknown"),dict(ActiveState="activating"),dict(ActiveState="failed"),
    dict(LoadState="loaded"),dict(FragmentPath="/run/other.service"),dict(DropInPaths="/run/override.conf")])
def test_contradictory_metadata_blocks_readonly_preflight(monkeypatch,tmp_path,capsys,change):
    monkeypatch.setattr(mod.os,"geteuid",lambda:0,raising=False)
    monkeypatch.setattr(mod,"verify_release",lambda *a,**k:None)
    monkeypatch.setattr(mod,"_verify_private_file",lambda *a,**k:None)
    root=tmp_path/"root";root.mkdir();(root/"current").mkdir()
    units=tmp_path/"units";units.mkdir()
    monkeypatch.setattr(mod,"ROOT",root);monkeypatch.setattr(mod,"UNIT_DIR",units)
    monkeypatch.setattr(mod,"unit_metadata",lambda name:ABSENT|change)
    assert mod.main(["--expected-shared","1"*40]) == 2
    value=json.loads(capsys.readouterr().out)
    assert value["result"] == "BLOCKED"
    assert value["release_identity"] == "VERIFIED"
    assert value["exporter_absence"] == "NOT_VERIFIED"
    assert any(item.startswith("UNIT_NOT_ABSENT:") for item in value["findings"])
    assert value["host_changes"] is False
    assert value["paper_isolation"] == "NOT_VERIFIED"
    assert list(units.iterdir()) == []


@pytest.mark.parametrize("status", [0,1,4])
def test_exact_absence_only_reaches_non_authorizing_review_required(monkeypatch,tmp_path,capsys,status):
    monkeypatch.setattr(mod.os,"geteuid",lambda:0,raising=False)
    monkeypatch.setattr(mod,"verify_release",lambda *a,**k:None)
    monkeypatch.setattr(mod,"_verify_private_file",lambda *a,**k:None)
    root=tmp_path/"root";root.mkdir();(root/"current").mkdir()
    units=tmp_path/"units";units.mkdir()
    state=tmp_path/"state";state.mkdir()
    monkeypatch.setattr(mod,"ROOT",root);monkeypatch.setattr(mod,"UNIT_DIR",units)
    monkeypatch.setattr(mod,"Path",lambda value:state if str(value)=="/var/lib/hope-dashboard-ops" else Path(value))
    calls=[]
    def observe(command,**kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command,status,encoded(ABSENT),b"")
    monkeypatch.setattr(mod,"_bounded_capture",observe)
    assert mod.main(["--expected-shared","1"*40]) == 2
    value=json.loads(capsys.readouterr().out)
    assert value["result"] == "REVIEW_REQUIRED"
    assert value["release_identity"] == "VERIFIED"
    assert value["exporter_absence"] == "VERIFIED"
    assert value["host_changes"] is False
    assert value["paper_isolation"] == value["database_identity"] == "NOT_VERIFIED"
    assert len(calls)==2
    assert all(command[:2]==["/usr/bin/systemctl","show"] for command in calls)
    assert list(units.iterdir()) == []


def test_configuration_failure_does_not_erase_verified_release_or_absence(monkeypatch,tmp_path,capsys):
    monkeypatch.setattr(mod.os,"geteuid",lambda:0,raising=False)
    monkeypatch.setattr(mod,"verify_release",lambda *a,**k:None)
    monkeypatch.setattr(mod,"_verify_private_file",lambda *a,**k:(_ for _ in ()).throw(ValueError("unsafe")))
    root=tmp_path/"root";root.mkdir();(root/"current").mkdir()
    units=tmp_path/"units";units.mkdir()
    state=tmp_path/"state";state.mkdir()
    monkeypatch.setattr(mod,"ROOT",root);monkeypatch.setattr(mod,"UNIT_DIR",units)
    monkeypatch.setattr(mod,"Path",lambda value:state if str(value)=="/var/lib/hope-dashboard-ops" else Path(value))
    monkeypatch.setattr(mod,"unit_metadata",lambda name:ABSENT)
    assert mod.main(["--expected-shared","1"*40]) == 2
    value=json.loads(capsys.readouterr().out)
    assert value["result"] == "BLOCKED"
    assert value["findings"] == ["CONFIG_UNVERIFIED"]
    assert value["release_identity"] == value["exporter_absence"] == "VERIFIED"


@pytest.mark.parametrize("status", [2,5,-9])
def test_unknown_exit_status_is_not_rehabilitated_by_lookalike_absence(monkeypatch,status):
    monkeypatch.setattr(mod,"_bounded_capture",lambda *a,**k:subprocess.CompletedProcess([],status,encoded(ABSENT),b""))
    with pytest.raises(ValueError,match="^UNIT_METADATA_UNVERIFIED$"):
        mod.unit_metadata(mod.NAMES[0])


@pytest.mark.parametrize("status", [1,4])
@pytest.mark.parametrize("payload", [b"",b"LoadState=not-found\n",
    encoded(ABSENT)+b"LoadState=not-found\n",encoded(ABSENT)+b"Other=value\n",
    b"\xff",b"x"*65537], ids=["empty","partial","duplicate","extra","invalid-utf8","oversized"])
def test_missing_status_requires_complete_bounded_unique_utf8_record(monkeypatch,status,payload):
    monkeypatch.setattr(mod,"_bounded_capture",lambda *a,**k:subprocess.CompletedProcess([],status,payload,b""))
    with pytest.raises((ValueError,UnicodeError)):
        mod.unit_metadata(mod.NAMES[0])
