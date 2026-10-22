"""Adversarial interrupted exporter installation recovery tests."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil

import pytest

REPO = Path(__file__).resolve().parents[1]
OPS = REPO / "deploy" / "operations"


def load(name):
    path = OPS / (name + ".py")
    spec = importlib.util.spec_from_file_location(name + "_recovery_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def interrupted(layout, count=2, *, published=True, phase="PUBLISHING"):
    inspector, root, operations, unit_dir, sha = layout
    entries = []
    for name in installer.NAMES[:count]:
        payload = (operations / name).read_bytes()
        if published:
            shutil.copyfile(operations / name, unit_dir / name)
            (unit_dir / name).chmod(0o644)
            info = (unit_dir / name).stat()
        else:
            temporary = unit_dir / (name + ".identity")
            temporary.write_bytes(payload)
            info = temporary.stat()
            temporary.unlink()
        identity = (info.st_dev, info.st_ino, info.st_size,
                    info.st_mtime_ns, info.st_ctime_ns)
        entries.append((name, identity, hashlib.sha256(payload).hexdigest(), published))
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    journal = journal_mod.InstallJournal(
        root_fd, root, sha, unit_dir, owner_uid=os.getuid()
    ).start()
    journal.record("STAGED", [])
    journal.record("PUBLISHING", entries)
    if phase == "RELOAD_PENDING":
        assert count == 2 and published
        journal.record("RELOAD_PENDING", entries)
    elif phase == "RECOVERY_PENDING":
        journal.record("RECOVERY_PENDING", entries)
    attempt = journal.data["attempt"]
    journal.close()
    os.close(root_fd)
    return attempt


def run(layout, attempt, reloads=None):
    inspector, root, _, unit_dir, sha = layout
    reloads = [] if reloads is None else reloads
    return recovery.recover_exporter_install(
        sha, attempt, root=root, unit_dir=unit_dir, owner_uid=os.getuid(),
        daemon_reload=lambda: reloads.append("reload"), inspector=inspector,
        installer=installer, journal_module=journal_mod,
    )


@pytest.mark.parametrize("count,phase", [(1, "PUBLISHING"), (2, "RELOAD_PENDING")])
def test_authenticated_interrupted_units_are_removed_and_terminal_evidence_retained(
    layout, count, phase
):
    attempt = interrupted(layout, count, phase=phase)
    reloads = []
    assert run(layout, attempt, reloads) == installer.NAMES[:count]
    assert reloads == ["reload"]
    assert all(not (layout[3] / name).exists() for name in installer.NAMES)
    value = journal_mod.inspect_journal(layout[1], owner_uid=os.getuid())
    assert value["phase"] == "RECOVERED"
    assert value["attempt"] == attempt
    with pytest.raises(journal_mod.JournalError, match="RECOVERY_REQUIRED"):
        fd = os.open(layout[1], os.O_RDONLY | os.O_DIRECTORY)
        try:
            journal_mod.InstallJournal(fd, layout[1], layout[4], layout[3],
                                       owner_uid=os.getuid()).start()
        finally:
            os.close(fd)


def test_byte_identical_replacement_inode_is_preserved_and_recovery_blocks(layout):
    attempt = interrupted(layout, 1)
    path = layout[3] / installer.NAMES[0]
    replacement = path.with_suffix(".replacement")
    replacement.write_bytes(path.read_bytes())
    replacement.chmod(0o644)
    os.replace(replacement, path)
    with pytest.raises(recovery.RecoveryError, match="ROLLBACK_UNVERIFIED"):
        run(layout, attempt)
    assert path.is_file()
    value = journal_mod.inspect_journal(layout[1], owner_uid=os.getuid())
    assert value["phase"] == "ROLLBACK_UNVERIFIED"


def test_unjournaled_peer_conflict_blocks_before_any_deletion(layout):
    attempt = interrupted(layout, 1)
    first = layout[3] / installer.NAMES[0]
    peer = layout[3] / installer.NAMES[1]
    peer.write_bytes(b"foreign peer\n")
    peer.chmod(0o644)
    with pytest.raises(recovery.RecoveryError, match="PEER_CONFLICT"):
        run(layout, attempt)
    assert first.is_file() and peer.read_bytes() == b"foreign peer\n"
    assert journal_mod.inspect_journal(
        layout[1], owner_uid=os.getuid()
    )["phase"] == "PUBLISHING"


@pytest.mark.parametrize("mismatch,error", [
    ("attempt", journal_mod.JournalError), ("sha", FileNotFoundError)
])
def test_operator_must_bind_exact_reviewed_attempt_and_release(layout, mismatch, error):
    attempt = interrupted(layout, 1)
    inspector, root, _, unit_dir, sha = layout
    with pytest.raises(error):
        recovery.recover_exporter_install(
            ("2" * 40 if mismatch == "sha" else sha),
            ("b" * 32 if mismatch == "attempt" else attempt),
            root=root, unit_dir=unit_dir, owner_uid=os.getuid(),
            daemon_reload=lambda: None, inspector=inspector,
            installer=installer, journal_module=journal_mod,
        )
    assert (unit_dir / installer.NAMES[0]).is_file()


def test_ambiguous_crash_between_link_and_confirmed_identity_never_deletes(layout):
    attempt = interrupted(layout, 1, published=False)
    shutil.copyfile(layout[2] / installer.NAMES[0], layout[3] / installer.NAMES[0])
    (layout[3] / installer.NAMES[0]).chmod(0o644)
    with pytest.raises(recovery.RecoveryError, match="PUBLICATION_AMBIGUOUS"):
        run(layout, attempt)
    assert (layout[3] / installer.NAMES[0]).is_file()


def test_unpublished_intent_with_absent_path_can_be_closed_without_deletion(layout):
    attempt = interrupted(layout, 1, published=False)
    reloads = []
    assert run(layout, attempt, reloads) == installer.NAMES[:1]
    assert reloads == []
    assert journal_mod.inspect_journal(
        layout[1], owner_uid=os.getuid()
    )["phase"] == "RECOVERED"


def test_recovery_pending_after_process_loss_is_idempotently_resumed(layout):
    attempt = interrupted(layout, 1, phase="RECOVERY_PENDING")
    assert run(layout, attempt) == installer.NAMES[:1]
    assert journal_mod.inspect_journal(
        layout[1], owner_uid=os.getuid()
    )["phase"] == "RECOVERED"


def test_cli_requires_root_and_explicit_approval(monkeypatch, capsys):
    monkeypatch.setattr(recovery.os, "geteuid", lambda: 1000)
    assert recovery.main([
        "--expected-shared", "1" * 40, "--expected-attempt", "a" * 32
    ]) == 2
    assert "EXPLICIT_ROOT_APPROVAL_REQUIRED" in capsys.readouterr().out


def test_v1_evidence_remains_inspectable_but_cannot_authorize_recovery(layout):
    record = dict(version=1, attempt="a"*32, sha=layout[4],
                  unit_directory=str(layout[3]), sequence=2,
                  phase="PUBLISHING", units=[dict(
                      name=installer.NAMES[0], device=1, inode=2,
                      sha256="a"*64)])
    assert journal_mod.decode_record(json.dumps(record).encode())["version"] == 1
