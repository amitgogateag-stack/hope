"""Adversarial terminal exporter recovery finalization tests."""
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil

import pytest

REPO = Path(__file__).resolve().parents[1]
OPS = REPO / "deploy" / "operations"


def load(name):
    path = OPS / (name + ".py")
    spec = importlib.util.spec_from_file_location(name + "_finalization_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


finalizer = load("finalize_exporter_recovery")
recovery = load("recover_exporter_install")
installer = load("install_exporter_units")
journal_mod = load("exporter_install_journal")


@pytest.fixture
def layout(tmp_path):
    inspector = load("inspect_exporter_install")
    sha = "1" * 40
    root = tmp_path / "root"
    operations = root / "releases" / sha / "deploy" / "operations"
    operations.mkdir(parents=True)
    for path in (root, root / "releases", root / "releases" / sha,
                 root / "releases" / sha / "deploy", operations):
        path.chmod(0o750)
    for name in installer.NAMES:
        shutil.copyfile(OPS / name, operations / name)
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
    inspector.unit_metadata = lambda name: dict(inspector.ABSENT_UNIT)
    return inspector, root, operations, unit_dir, sha


def make_interrupted(layout):
    _, root, operations, unit_dir, sha = layout
    name = installer.NAMES[0]
    shutil.copyfile(operations / name, unit_dir / name)
    (unit_dir / name).chmod(0o644)
    info = (unit_dir / name).stat()
    payload = (operations / name).read_bytes()
    entry = (name, (info.st_dev, info.st_ino, info.st_size,
                    info.st_mtime_ns, info.st_ctime_ns),
             hashlib.sha256(payload).hexdigest())
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    journal = journal_mod.InstallJournal(
        root_fd, root, sha, unit_dir, owner_uid=os.getuid()
    ).start()
    journal.record("STAGED", [])
    journal.record("PUBLISHING", [entry])
    attempt = journal.data["attempt"]
    journal.close()
    os.close(root_fd)
    return attempt


def make_recovered(layout):
    attempt = make_interrupted(layout)
    inspector, root, _, unit_dir, sha = layout
    recovery.recover_exporter_install(
        sha, attempt, root=root, unit_dir=unit_dir, owner_uid=os.getuid(),
        daemon_reload=lambda: None, inspector=inspector,
        installer=installer, journal_module=journal_mod,
    )
    return attempt


def finalize(layout, attempt):
    inspector, root, _, unit_dir, sha = layout
    return finalizer.finalize_exporter_recovery(
        sha, attempt, root=root, unit_dir=unit_dir, owner_uid=os.getuid(),
        inspector=inspector, installer=installer, journal_module=journal_mod,
    )


def test_exact_recovered_evidence_is_archived_and_new_attempt_can_start(layout):
    attempt = make_recovered(layout)
    archive = finalize(layout, attempt)
    archived_path = layout[1] / archive
    assert archived_path.is_file()
    assert not (layout[1] / journal_mod.JOURNAL).exists()
    value = journal_mod.decode_record(archived_path.read_bytes())
    assert value["phase"] == "RECOVERED" and value["attempt"] == attempt

    root_fd = os.open(layout[1], os.O_RDONLY | os.O_DIRECTORY)
    journal = journal_mod.InstallJournal(
        root_fd, layout[1], layout[4], layout[3], owner_uid=os.getuid()
    ).start()
    try:
        assert journal.data["attempt"] != attempt
        assert journal.data["phase"] == "PREPARED"
    finally:
        journal.close()
        os.close(root_fd)


def test_finalization_is_idempotent_after_terminal_archive(layout):
    attempt = make_recovered(layout)
    archive = finalize(layout, attempt)
    assert finalize(layout, attempt) == archive


def test_nonterminal_evidence_cannot_be_finalized(layout):
    attempt = make_interrupted(layout)
    with pytest.raises(journal_mod.JournalError, match="EVIDENCE_MISMATCH"):
        finalize(layout, attempt)
    assert (layout[1] / journal_mod.JOURNAL).is_file()
    assert (layout[3] / installer.NAMES[0]).is_file()


@pytest.mark.parametrize("conflict", ["path", "runtime"])
def test_unit_conflict_preserves_terminal_journal(layout, conflict):
    attempt = make_recovered(layout)
    if conflict == "path":
        (layout[3] / installer.NAMES[1]).write_text("foreign\n")
    else:
        layout[0].unit_metadata = lambda name: {
            **layout[0].ABSENT_UNIT, "ActiveState": "active"
        }
    with pytest.raises(finalizer.FinalizationError, match="UNIT_NOT_ABSENT"):
        finalize(layout, attempt)
    assert (layout[1] / journal_mod.JOURNAL).is_file()


def test_wrong_attempt_cannot_select_terminal_evidence(layout):
    attempt = make_recovered(layout)
    with pytest.raises(journal_mod.JournalError, match="EVIDENCE_MISMATCH"):
        finalize(layout, "b" * 32)
    assert journal_mod.inspect_journal(
        layout[1], owner_uid=os.getuid()
    )["attempt"] == attempt


def test_concurrent_finalizer_cannot_share_terminal_authority(layout):
    attempt = make_recovered(layout)
    root_fd = os.open(layout[1], os.O_RDONLY | os.O_DIRECTORY)
    held = journal_mod.InstallJournal.open_finalization(
        root_fd, layout[1], layout[4], layout[3], attempt,
        owner_uid=os.getuid(),
    )
    try:
        with pytest.raises(journal_mod.JournalError, match="CONCURRENT"):
            finalize(layout, attempt)
    finally:
        held.close()
        os.close(root_fd)
    assert (layout[1] / journal_mod.JOURNAL).is_file()


def test_release_change_during_absence_checks_preserves_terminal_journal(layout):
    attempt = make_recovered(layout)
    other = layout[1] / "releases" / ("2" * 40)
    other.mkdir()
    other.chmod(0o750)
    changed = False

    def change_current(name):
        nonlocal changed
        if not changed:
            current = layout[1] / "current"
            current.unlink()
            current.symlink_to(other)
            changed = True
        return dict(layout[0].ABSENT_UNIT)

    layout[0].unit_metadata = change_current
    with pytest.raises(ValueError):
        finalize(layout, attempt)
    assert (layout[1] / journal_mod.JOURNAL).is_file()


def test_foreign_archive_collision_blocks_without_removing_journal(layout):
    attempt = make_recovered(layout)
    archive = layout[1] / (journal_mod.ARCHIVE_PREFIX + attempt + ".json")
    archive.write_text("foreign\n")
    archive.chmod(0o600)
    with pytest.raises(journal_mod.JournalError):
        finalize(layout, attempt)
    assert archive.read_text() == "foreign\n"
    assert (layout[1] / journal_mod.JOURNAL).is_file()


def test_process_loss_after_archive_link_resumes_without_losing_evidence(
    layout, monkeypatch
):
    attempt = make_recovered(layout)
    original = journal_mod.os.unlink

    def interrupt(name, *args, **kwargs):
        if name == journal_mod.JOURNAL:
            raise OSError("simulated process loss before live-name removal")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(journal_mod.os, "unlink", interrupt)
    with pytest.raises(OSError, match="simulated process loss"):
        finalize(layout, attempt)
    archive = layout[1] / (journal_mod.ARCHIVE_PREFIX + attempt + ".json")
    live = layout[1] / journal_mod.JOURNAL
    assert archive.stat().st_ino == live.stat().st_ino
    assert live.stat().st_nlink == 2

    monkeypatch.setattr(journal_mod.os, "unlink", original)
    assert finalize(layout, attempt) == archive.name
    assert archive.is_file() and not live.exists()
    assert archive.stat().st_nlink == 1


def test_cli_requires_root_and_explicit_approval(monkeypatch, capsys):
    monkeypatch.setattr(finalizer.os, "geteuid", lambda: 1000)
    assert finalizer.main([
        "--expected-shared", "1" * 40,
        "--expected-attempt", "a" * 32,
    ]) == 2
    assert "EXPLICIT_ROOT_APPROVAL_REQUIRED" in capsys.readouterr().out
