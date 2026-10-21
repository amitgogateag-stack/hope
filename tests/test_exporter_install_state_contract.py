"""Installer state gates use API doubles and never touch host systemd."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE=Path(__file__).parents[1]/"deploy/operations/install_exporter_units.py"
spec=importlib.util.spec_from_file_location("exporter_install_state_contract",SOURCE)
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
ABSENT=dict(LoadState="not-found",ActiveState="inactive",UnitFileState="",FragmentPath="",DropInPaths="")


def installed_metadata(name,unit_dir):
    service = name.endswith(".service")
    return dict(LoadState="loaded",ActiveState="inactive",UnitFileState="static" if service else "disabled",
                FragmentPath=str(unit_dir/name),DropInPaths="")


@pytest.mark.parametrize("change",[dict(LoadState="loaded"),dict(ActiveState="active"),
    dict(ActiveState="activating"),dict(UnitFileState="enabled"),dict(UnitFileState="unknown"),
    dict(FragmentPath="/usr/lib/systemd/system/other.service"),dict(DropInPaths="/run/override.conf")])
def test_preexisting_systemd_state_blocks_before_opening_any_install_directory(change):
    calls=[]
    def forbidden(*a,**k):
        calls.append("open")
        raise AssertionError("filesystem stage reached before runtime absence was verified")
    inspector=SimpleNamespace(verify_release=lambda *a,**k:None,
        unit_metadata=lambda name:ABSENT|change,_open_directory=forbidden,ABSENT_UNIT=ABSENT)
    with pytest.raises(mod.InstallError,match="UNIT_NOT_ABSENT"):
        mod.install_exporter_units("1"*40,inspector=inspector)
    assert calls==[]


@pytest.mark.parametrize("failure",[ValueError("private"),OSError("private")])
def test_metadata_failure_is_sanitized_before_installation(failure):
    def broken(name): raise failure
    inspector=SimpleNamespace(unit_metadata=broken,ABSENT_UNIT=ABSENT)
    with pytest.raises(mod.InstallError,match="^EXPORTER_UNIT_STATE_UNVERIFIED$"):
        mod._assert_units_absent(inspector)


@pytest.mark.parametrize("name",mod.NAMES)
@pytest.mark.parametrize("change",[dict(ActiveState="active"),dict(ActiveState="activating"),
    dict(ActiveState="failed"),dict(UnitFileState="enabled"),dict(UnitFileState="unknown"),
    dict(FragmentPath="/usr/lib/vendor.service"),dict(DropInPaths="/run/other.conf"),dict(LoadState="not-found")])
def test_installed_state_must_be_exact_and_quiescent(name,change,tmp_path):
    def observed(unit):
        value=installed_metadata(unit,tmp_path)
        return value|change if unit==name else value
    with pytest.raises(mod.InstallError,match="INSTALLED_STATE_UNVERIFIED"):
        mod._assert_installed_quiescent(SimpleNamespace(unit_metadata=observed),tmp_path)


def test_only_static_service_and_disabled_timer_satisfy_success(tmp_path):
    inspector=SimpleNamespace(unit_metadata=lambda name:installed_metadata(name,tmp_path))
    mod._assert_installed_quiescent(inspector,tmp_path)


@pytest.mark.parametrize("change",[dict(ActiveState="active"),dict(ActiveState="activating"),
    dict(UnitFileState="enabled"),dict(DropInPaths="/run/other.conf")])
def test_rollback_never_reads_or_unlinks_an_active_or_unverified_unit(monkeypatch,tmp_path,change):
    name=mod.NAMES[0]
    def forbidden(*a,**k): raise AssertionError("rollback touched unverified unit")
    inspector=SimpleNamespace(ABSENT_UNIT=ABSENT,
        unit_metadata=lambda unit:installed_metadata(unit,tmp_path)|change,
        _read_regular_at=forbidden)
    monkeypatch.setattr(mod.os,"unlink",forbidden)
    with pytest.raises(mod.InstallError,match="^EXPORTER_ROLLBACK_UNVERIFIED$"):
        mod._rollback(99,[(name,(1,2),"digest")],{name:b"payload"},inspector,owner_uid=0,unit_dir=tmp_path)
