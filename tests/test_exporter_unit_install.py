"""Adversarial tests for exporter-only unit installation."""
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil

import pytest

REPO = Path(__file__).resolve().parents[1]
OPERATIONS = REPO / "deploy" / "operations"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load(
    OPERATIONS / "install_exporter_units.py", "exporter_installer_test"
)


def installed_metadata(name,unit_dir):
    service = name.endswith(".service")
    return dict(LoadState="loaded",ActiveState="inactive",UnitFileState="static" if service else "disabled",
                FragmentPath=str(unit_dir/name),DropInPaths="")


@pytest.fixture
def layout(tmp_path):
    inspector = load(
        OPERATIONS / "inspect_exporter_install.py", "exporter_inspector_install_test"
    )
    sha = "1" * 40
    root = tmp_path / "root"
    operations = root / "releases" / sha / "deploy" / "operations"
    operations.mkdir(parents=True)
    for path in (root, root / "releases", root / "releases" / sha,
                 root / "releases" / sha / "deploy", operations):
        path.chmod(0o750)
    for name in installer.NAMES:
        source = OPERATIONS / name
        shutil.copyfile(source, operations / name)
        (operations / name).chmod(0o600)
    (root / "deployed.env").write_text(f"HOPE_DEPLOYED_COMMIT={sha}\n")
    (root / "deployed.env").chmod(0o600)
    (root / "current").symlink_to(root / "releases" / sha)
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    unit_dir.chmod(0o755)
    inspector.EXPECTED_TEMPLATE_HASHES = {
        name: hashlib.sha256((operations / name).read_bytes()).hexdigest()
        for name in installer.NAMES
    }
    inspector._test_reloaded = False
    def observed_state(name):
        if not inspector._test_reloaded or not (unit_dir / name).exists():
            return dict(inspector.ABSENT_UNIT)
        return installed_metadata(name, unit_dir)
    inspector.unit_metadata = observed_state
    return inspector, root, unit_dir, sha


def install(layout, reload=lambda: None):
    inspector, root, unit_dir, sha = layout
    def simulated_reload():
        try:
            reload()
        finally:
            inspector._test_reloaded = True
    return installer.install_exporter_units(
        sha,
        root=root,
        unit_dir=unit_dir,
        owner_uid=os.getuid(),
        daemon_reload=simulated_reload,
        inspector=inspector,
    )


def test_installs_only_exporter_units_disabled(layout):
    calls = []
    assert install(layout, lambda: calls.append("reload")) == installer.NAMES
    _, root, unit_dir, sha = layout
    assert calls == ["reload"]
    assert sorted(path.name for path in unit_dir.iterdir()) == sorted(installer.NAMES)
    for name in installer.NAMES:
        assert (unit_dir / name).read_bytes() == (
            root / "releases" / sha / "deploy" / "operations" / name
        ).read_bytes()
        assert (unit_dir / name).stat().st_mode & 0o777 == 0o644
    assert not (unit_dir / "hope-paper.service").exists()


@pytest.mark.parametrize("kind", ["regular", "symlink"])
def test_existing_unit_path_blocks_without_partial_install(layout, kind):
    _, _, unit_dir, _ = layout
    target = unit_dir / installer.NAMES[1]
    if kind == "regular":
        target.write_text("unreviewed")
    else:
        target.symlink_to(unit_dir / "missing")
    with pytest.raises(installer.InstallError, match="UNIT_ALREADY_EXISTS"):
        install(layout)
    assert not (unit_dir / installer.NAMES[0]).exists()
    assert target.is_symlink() if kind == "symlink" else target.read_text() == "unreviewed"


def test_daemon_reload_failure_rolls_back_exact_installed_inodes(layout):
    _, _, unit_dir, _ = layout
    def fail():
        raise RuntimeError("synthetic reload failure")
    with pytest.raises(installer.InstallError, match="^EXPORTER_ROLLBACK_UNVERIFIED$"):
        install(layout, fail)
    assert list(unit_dir.iterdir()) == []


def test_replaced_installed_unit_is_never_deleted_by_rollback(layout):
    _, _, unit_dir, _ = layout
    replacement = b"attacker replacement\n"
    def replace_then_fail():
        path = unit_dir / installer.NAMES[0]
        path.unlink()
        path.write_bytes(replacement)
        path.chmod(0o644)
        raise RuntimeError("synthetic reload failure")
    with pytest.raises(installer.InstallError, match="ROLLBACK_UNVERIFIED"):
        install(layout, replace_then_fail)
    assert (unit_dir / installer.NAMES[0]).read_bytes() == replacement


def test_systemd_directory_replacement_before_publication_fails_closed(layout, monkeypatch):
    inspector, _, unit_dir, _ = layout
    original = inspector._verify_directory_path
    moved = unit_dir.with_name("systemd-original")
    calls = 0
    def replace(path, fd, *, owner_uid):
        nonlocal calls
        calls += 1
        if calls == 2:
            unit_dir.rename(moved)
            unit_dir.mkdir()
        return original(path, fd, owner_uid=owner_uid)
    monkeypatch.setattr(inspector, "_verify_directory_path", replace)
    with pytest.raises(ValueError, match="DIRECTORY_CHANGED"):
        install(layout)
    assert list(unit_dir.iterdir()) == []
    assert not any(path.name in installer.NAMES for path in moved.iterdir())


def test_cli_requires_root_and_explicit_approval(monkeypatch, capsys):
    monkeypatch.setattr(installer.os, "geteuid", lambda: 1000)
    assert installer.main(["--expected-shared", "1" * 40]) == 2
    assert "EXPLICIT_ROOT_APPROVAL_REQUIRED" in capsys.readouterr().out



def test_runtime_unit_appearing_before_publication_aborts_and_cleans_staging(layout,monkeypatch):
    inspector,_,unit_dir,_=layout
    original=inspector._verify_directory_path
    calls=0
    reloads=[]
    def after_staging(path,fd,*,owner_uid):
        nonlocal calls
        calls+=1
        result=original(path,fd,owner_uid=owner_uid)
        if calls==2:
            inspector.unit_metadata=lambda name:dict(inspector.ABSENT_UNIT,ActiveState="active")
        return result
    monkeypatch.setattr(inspector,"_verify_directory_path",after_staging)
    with pytest.raises(installer.InstallError,match="UNIT_NOT_ABSENT"):
        install(layout,lambda:reloads.append(True))
    assert reloads==[]
    assert list(unit_dir.iterdir())==[]


@pytest.mark.parametrize("name,change",[(installer.NAMES[0],dict(ActiveState="active")),
    (installer.NAMES[1],dict(UnitFileState="enabled")),
    (installer.NAMES[0],dict(FragmentPath="/usr/lib/vendor.service"))])
def test_post_reload_conflict_is_not_reported_success_or_deleted(layout,name,change):
    inspector,_,unit_dir,_=layout
    def conflicting_reload():
        inspector.unit_metadata=lambda unit:(installed_metadata(unit,unit_dir)|change
            if unit==name else installed_metadata(unit,unit_dir))
    with pytest.raises(installer.InstallError,match="^EXPORTER_ROLLBACK_UNVERIFIED$"):
        install(layout,conflicting_reload)
    assert (unit_dir/name).is_file()
    assert (unit_dir/name).read_bytes()
    assert not any(path.name.endswith(".next") for path in unit_dir.iterdir())


@pytest.mark.parametrize(
    "interruption_type",
    [RuntimeError, KeyboardInterrupt, SystemExit],
)
def test_python_unwind_after_atomic_publish_rolls_back_tracked_inode(
    layout, monkeypatch, interruption_type
):
    _, _, unit_dir, _ = layout
    original_link = installer.os.link
    calls = 0

    def publish_then_fail(*args, **kwargs):
        nonlocal calls
        calls += 1
        original_link(*args, **kwargs)
        if calls == 1:
            raise interruption_type("synthetic post-publish interruption")

    monkeypatch.setattr(installer.os, "link", publish_then_fail)
    with pytest.raises(interruption_type, match="post-publish interruption"):
        install(layout)
    assert list(unit_dir.iterdir()) == []


def test_staging_replacement_before_publication_is_preserved(layout, monkeypatch):
    _, _, unit_dir, _ = layout
    original_absence_check = installer._assert_units_absent
    calls = 0
    replacement = b"unexpected staging replacement\n"
    replaced_path = None

    def replace_after_staging(inspector):
        nonlocal calls, replaced_path
        calls += 1
        result = original_absence_check(inspector)
        if calls == 2:
            replaced_path = next(unit_dir.glob(".*.next"))
            replaced_path.unlink()
            replaced_path.write_bytes(replacement)
            replaced_path.chmod(0o644)
        return result

    monkeypatch.setattr(installer, "_assert_units_absent", replace_after_staging)
    with pytest.raises(
        installer.InstallError,
        match="^EXPORTER_STAGE_CLEANUP_UNVERIFIED$",
    ):
        install(layout)
    assert replaced_path is not None
    assert replaced_path.read_bytes() == replacement
    assert not any((unit_dir / name).exists() for name in installer.NAMES)


def test_staging_replacement_after_publication_is_preserved(layout, monkeypatch):
    _, _, unit_dir, _ = layout
    original_link = installer.os.link
    replacement = b"post-publication staging replacement\n"
    replaced_path = None

    def publish_then_replace(source, destination, **kwargs):
        nonlocal replaced_path
        original_link(source, destination, **kwargs)
        if replaced_path is None:
            replaced_path = unit_dir / source
            replaced_path.unlink()
            replaced_path.write_bytes(replacement)
            replaced_path.chmod(0o644)

    monkeypatch.setattr(installer.os, "link", publish_then_replace)
    with pytest.raises(installer.InstallError, match="STAGE_CLEANUP_UNVERIFIED"):
        install(layout)
    assert replaced_path is not None
    assert replaced_path.read_bytes() == replacement
    assert not any((unit_dir / name).exists() for name in installer.NAMES)


def test_cleanup_refuses_replaced_staging_inode(layout):
    _, _, unit_dir, _ = layout
    path = unit_dir / ".untrusted.next"
    path.write_bytes(b"created by installer")
    original = path.stat()
    entry = (path.name, (original.st_dev, original.st_ino))
    path.unlink()
    path.write_bytes(b"replacement")

    staged = [entry]
    directory_fd = os.open(unit_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(
            installer.InstallError,
            match="^EXPORTER_STAGE_CLEANUP_UNVERIFIED$",
        ):
            installer._cleanup_staged(directory_fd, staged)
    finally:
        os.close(directory_fd)
    assert path.read_bytes() == b"replacement"
    assert staged == [entry]
