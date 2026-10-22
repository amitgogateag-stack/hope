"""Private journal parsing, ownership, interruption and durability contracts."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).parents[1] / "deploy/operations/exporter_install_journal.py"
spec = importlib.util.spec_from_file_location("exporter_journal_test", SOURCE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def record():
    return dict(version=1, attempt="a" * 32, sha="1" * 40,
                unit_directory="/etc/systemd/system", sequence=0,
                phase="PREPARED", units=[])


def test_valid_record_roundtrip():
    assert mod.decode_record(json.dumps(record()).encode()) == record()


@pytest.mark.parametrize("change", [dict(version=True), dict(sequence=True),
    dict(sequence=-1), dict(sequence=33), dict(phase="UNKNOWN"), dict(phase=[]),
    dict(sha="bad"), dict(attempt="bad"), dict(unit_directory="relative"),
    dict(unit_directory="/a\0b"), dict(units={}), dict(extra=True),
    dict(phase="COMMITTED"), dict(phase="RELOAD_PENDING")])
def test_invalid_schema(change):
    value = record() | change
    with pytest.raises(mod.JournalError):
        mod.decode_record(json.dumps(value).encode())


@pytest.mark.parametrize("payload", [b"", b"{", b"\xff", b" " * (mod.MAX_BYTES + 1),
    b'{"version":1,"version":1}', b'[]', b'null'])
def test_invalid_encoding_and_duplicate_keys(payload):
    with pytest.raises(mod.JournalError):
        mod.decode_record(payload)


@pytest.mark.parametrize("change", [dict(name="hope-paper.service"), dict(name=[]),
    dict(device=True), dict(inode=0), dict(inode=True), dict(sha256="bad"),
    dict(extra=True)])
def test_unit_evidence_is_strict(change):
    value = record()
    value["units"] = [dict(name=mod.NAMES[0], device=1, inode=1, sha256="a"*64) | change]
    with pytest.raises(mod.JournalError):
        mod.decode_record(json.dumps(value).encode())


@pytest.mark.parametrize("mismatch", [False, True])
def test_inspector_never_authorizes_recovery(mismatch, monkeypatch, capsys):
    path = SOURCE.with_name("inspect_exporter_install_journal.py")
    spec = importlib.util.spec_from_file_location("journal_cli_test", path)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    calls = []
    def inspect(root):
        calls.append(root)
        return record()
    fake = SimpleNamespace(inspect_journal=inspect)
    monkeypatch.setattr(cli.importlib.util, "module_from_spec", lambda s: fake)
    monkeypatch.setattr(cli.importlib.util, "spec_from_file_location",
                        lambda *a: SimpleNamespace(loader=SimpleNamespace(exec_module=lambda m: None)))
    assert cli.main(["--expected-shared", ("2" if mismatch else "1")*40]) == 2
    output = capsys.readouterr().out
    assert calls == [Path("/opt/hope-operations")]
    if mismatch:
        assert output.strip() == "EXPORTER_JOURNAL_INSPECTION_BLOCKED"
    else:
        value = json.loads(output)
        assert value["status"] == "REVIEW_REQUIRED"
        assert value["recovery_authorized"] is False
        assert value["host_changes"] is False
        assert value["live_enabled"] is False


@pytest.fixture
def native(tmp_path):
    if os.name != "posix":
        pytest.skip("native POSIX directory durability and flock")
    tmp_path.chmod(0o750)
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    journal = mod.InstallJournal(fd, tmp_path, "1" * 40, Path("/etc/systemd/system"), owner_uid=os.getuid())
    try:
        yield tmp_path, fd, journal
    finally:
        journal.close()
        os.close(fd)


def test_serialized_owner_and_existing_evidence_block_retry(native):
    root, fd, journal = native
    journal.start()
    other = mod.InstallJournal(fd, root, "1"*40, Path("/etc/systemd/system"), owner_uid=os.getuid())
    with pytest.raises(mod.JournalError, match="CONCURRENT"):
        other.start()
    journal.close()
    with pytest.raises(mod.JournalError, match="RECOVERY_REQUIRED"):
        other.start()
    assert mod.inspect_journal(root, owner_uid=os.getuid())["phase"] == "PREPARED"


def test_lifecycle_and_readonly_inspection(native):
    root, _, journal = native
    journal.start()
    journal.record("STAGED", [])
    entries = []
    for i, name in enumerate(mod.NAMES):
        entries.append((name, (1, i+1, 1, 1, 1), "a"*64))
        journal.record("PUBLISHING", entries)
    journal.record("RELOAD_PENDING", entries)
    journal.record("COMMITTED", entries)
    with pytest.raises(mod.JournalError, match="TRANSITION"):
        journal.record("STAGED", [])
    journal.close()
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    value = mod.inspect_journal(root, owner_uid=os.getuid())
    assert value["phase"] == "COMMITTED" and value["sequence"] == 5
    assert before == {p.name: p.read_bytes() for p in root.iterdir()}


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "public", "replaced"])
def test_untrusted_journal_rejected(native, kind):
    root, _, journal = native
    journal.start()
    journal.close()
    path = root/mod.JOURNAL
    if kind == "public":
        path.chmod(0o644)
    elif kind == "hardlink":
        os.link(path, root/"duplicate")
    else:
        path.rename(root/"original")
        if kind == "symlink":
            path.symlink_to(root/"original")
        else:
            path.write_text("corrupt")
            path.chmod(0o600)
    with pytest.raises((mod.JournalError, OSError)):
        mod.inspect_journal(root, owner_uid=os.getuid())


def test_replaced_guard_blocks_next_write(native):
    root, _, journal = native
    journal.start()
    (root/mod.GUARD).rename(root/"old-guard")
    (root/mod.GUARD).write_bytes(b"")
    (root/mod.GUARD).chmod(0o600)
    with pytest.raises(mod.JournalError, match="GUARD_CHANGED"):
        journal.record("STAGED", [])


@pytest.mark.parametrize("directory_failure", [False, True])
def test_sync_failure_never_acknowledged(native, monkeypatch, directory_failure):
    root, directory_fd, journal = native
    journal.start()
    original = os.fsync
    def fail(fd):
        if (fd == directory_fd) == directory_failure:
            raise OSError("injected sync failure")
        return original(fd)
    monkeypatch.setattr(mod.os, "fsync", fail)
    with pytest.raises(OSError):
        journal.record("STAGED", [])
    assert not journal.healthy
    assert journal.data["phase"] == "PREPARED"
    journal.close()
    value = mod.inspect_journal(root, owner_uid=os.getuid())
    assert value["phase"] == ("STAGED" if directory_failure else "PREPARED")


def test_process_death_preserves_write_ahead_intent(native):
    root, _, _ = native
    script = '''import importlib.util,os,sys
from pathlib import Path
s=importlib.util.spec_from_file_location("j",sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
r=Path(sys.argv[2]);fd=os.open(r,os.O_RDONLY|os.O_DIRECTORY)
j=m.InstallJournal(fd,r,"1"*40,Path("/etc/systemd/system"),owner_uid=os.getuid()).start()
j.record("STAGED",[])
j.record("PUBLISHING",[(m.NAMES[0],(1,2,1,1,1),"a"*64)])
os._exit(137)
'''
    result = subprocess.run([sys.executable, "-c", script, str(SOURCE), str(root)], timeout=10,
                            capture_output=True)
    assert result.returncode == 137
    value = mod.inspect_journal(root, owner_uid=os.getuid())
    assert value["phase"] == "PUBLISHING" and len(value["units"]) == 1
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    retry = mod.InstallJournal(fd, root, "1"*40, Path("/etc/systemd/system"), owner_uid=os.getuid())
    try:
        with pytest.raises(mod.JournalError, match="RECOVERY_REQUIRED"):
            retry.start()
    finally:
        retry.close()
        os.close(fd)
