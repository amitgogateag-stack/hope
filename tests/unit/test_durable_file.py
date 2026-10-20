"""Crash-durability contract for operational file replacement."""
from pathlib import Path

import pytest

from hope.infrastructure import durable_file


def test_replace_opens_directory_before_rename_and_syncs_after(monkeypatch, tmp_path):
    source = tmp_path / ".snapshot.tmp"
    target = tmp_path / "snapshot.json"
    source.write_bytes(b"complete")
    calls = []
    real_open = durable_file.os.open
    real_replace = durable_file.os.replace
    real_fsync = durable_file.os.fsync
    real_close = durable_file.os.close

    def recorded_open(path, flags):
        fd = real_open(path, flags)
        calls.append(("open", Path(path), flags, fd))
        return fd

    def recorded_replace(old, new, **kwargs):
        calls.append(("replace", old, new, kwargs))
        return real_replace(old, new, **kwargs)

    def recorded_fsync(fd):
        calls.append(("fsync", fd))
        return real_fsync(fd)

    def recorded_close(fd):
        calls.append(("close", fd))
        return real_close(fd)

    monkeypatch.setattr(durable_file.os, "open", recorded_open)
    monkeypatch.setattr(durable_file.os, "replace", recorded_replace)
    monkeypatch.setattr(durable_file.os, "fsync", recorded_fsync)
    monkeypatch.setattr(durable_file.os, "close", recorded_close)
    durable_file.durable_replace(source, target)

    assert [call[0] for call in calls] == ["open", "replace", "fsync", "close"]
    assert calls[0][1] == tmp_path
    descriptor = calls[0][3]
    assert calls[1][1:] == (source.name, target.name,
                            {"src_dir_fd": descriptor, "dst_dir_fd": descriptor})
    assert calls[2:] == [("fsync", descriptor), ("close", descriptor)]
    assert target.read_bytes() == b"complete"


def test_directory_open_failure_preserves_prior_target(monkeypatch, tmp_path):
    source = tmp_path / ".snapshot.tmp"
    target = tmp_path / "snapshot.json"
    source.write_bytes(b"new")
    target.write_bytes(b"prior")
    monkeypatch.setattr(
        durable_file.os,
        "open",
        lambda *_: (_ for _ in ()).throw(OSError("directory sync unavailable")),
    )
    monkeypatch.setattr(
        durable_file.os,
        "replace",
        lambda *_: pytest.fail("replace must not occur when durability cannot be established"),
    )

    with pytest.raises(OSError, match="directory sync unavailable"):
        durable_file.durable_replace(source, target)
    assert target.read_bytes() == b"prior"
    assert source.read_bytes() == b"new"


@pytest.mark.parametrize("failure", ["replace", "fsync"])
def test_directory_descriptor_is_closed_on_publication_failure(monkeypatch, tmp_path, failure):
    source = tmp_path / ".snapshot.tmp"
    target = tmp_path / "snapshot.json"
    source.write_bytes(b"new")
    target.write_bytes(b"prior")
    closed = []
    real_open = durable_file.os.open
    real_close = durable_file.os.close
    real_replace = durable_file.os.replace
    monkeypatch.setattr(durable_file.os, "open", lambda path, flags: real_open(path, flags))

    def replace(*args, **kwargs):
        if failure == "replace":
            raise OSError("replace failed")
        return real_replace(*args, **kwargs)

    def fsync(*_):
        if failure == "fsync":
            raise OSError("fsync failed")

    monkeypatch.setattr(durable_file.os, "replace", replace)
    monkeypatch.setattr(durable_file.os, "fsync", fsync)
    monkeypatch.setattr(durable_file.os, "close", lambda fd: (closed.append(fd), real_close(fd)))

    with pytest.raises(OSError, match=f"{failure} failed"):
        durable_file.durable_replace(source, target)
    assert len(closed) == 1


def test_cross_directory_replace_is_rejected_before_open(monkeypatch, tmp_path):
    source = tmp_path / "source" / "pending"
    target = tmp_path / "target" / "current"
    source.parent.mkdir()
    target.parent.mkdir()
    monkeypatch.setattr(durable_file.os, "open", lambda *_: pytest.fail("must not open"))
    with pytest.raises(ValueError, match="DURABLE_REPLACE_DIRECTORY_MISMATCH"):
        durable_file.durable_replace(source, target)


def test_relative_target_accepts_absolute_temp_in_same_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / ".snapshot.tmp"
    target = Path("snapshot.json")
    source.write_bytes(b"complete")

    durable_file.durable_replace(source, target)

    assert target.read_bytes() == b"complete"
    assert not source.exists()


def test_parent_replacement_cannot_publish_into_wrong_directory(tmp_path, monkeypatch):
    """A swap after opening the directory must not redirect publication."""
    import os

    live = tmp_path / "live"
    live.mkdir()
    archived = tmp_path / "archived"
    substitute = tmp_path / "substitute"
    substitute.mkdir()
    (live / ".new").write_bytes(b"legitimate-new")
    (live / "snapshot.json").write_bytes(b"legitimate-old")
    (substitute / ".new").write_bytes(b"injected-new")
    (substitute / "snapshot.json").write_bytes(b"substitute-old")
    actual_replace = os.replace
    once = False

    def swap_before_rename(src, dst, **kwargs):
        nonlocal once
        if not once:
            once = True
            live.rename(archived)
            substitute.rename(live)
        return actual_replace(src, dst, **kwargs)

    monkeypatch.setattr(durable_file.os, "replace", swap_before_rename)
    with pytest.raises(ValueError, match="DURABLE_REPLACE_DIRECTORY_CHANGED"):
        durable_file.durable_replace(live / ".new", live / "snapshot.json")
    assert (live / "snapshot.json").read_bytes() == b"substitute-old"
    assert (archived / "snapshot.json").read_bytes() == b"legitimate-new"


def test_symlinked_parent_is_rejected(tmp_path):
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    (trusted / ".new").write_bytes(b"new")
    (trusted / "snapshot.json").write_bytes(b"old")
    alias = tmp_path / "alias"
    alias.symlink_to(trusted, target_is_directory=True)
    with pytest.raises((OSError, ValueError)):
        durable_file.durable_replace(alias / ".new", alias / "snapshot.json")
    assert (trusted / "snapshot.json").read_bytes() == b"old"

@pytest.mark.parametrize("source_kind", ["symlink", "hardlink", "fifo"])
def test_unsafe_source_is_rejected_without_replacing_prior_target(tmp_path, source_kind):
    import os

    secret = tmp_path / "secret"
    secret.write_bytes(b"not for publication")
    source = tmp_path / ".new"
    target = tmp_path / "snapshot.json"
    target.write_bytes(b"prior")
    if source_kind == "symlink":
        source.symlink_to(secret)
    elif source_kind == "hardlink":
        os.link(secret, source)
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO requires POSIX")
        os.mkfifo(source)
    with pytest.raises(ValueError, match="DURABLE_REPLACE_UNSAFE_SOURCE"):
        durable_file.durable_replace(source, target)
    assert target.read_bytes() == b"prior"
    assert secret.read_bytes() == b"not for publication"


def test_identical_source_target_refused(tmp_path):
    target = tmp_path / "snapshot.json"
    target.write_bytes(b"prior")
    with pytest.raises(ValueError, match="DURABLE_REPLACE_IDENTICAL_PATH"):
        durable_file.durable_replace(target, target)
    assert target.read_bytes() == b"prior"


def test_source_substitution_before_rename_preserves_prior_target(tmp_path, monkeypatch):
    import os

    source = tmp_path / ".new"
    source.write_bytes(b"complete")
    secret = tmp_path / "secret"
    secret.write_bytes(b"untrusted")
    target = tmp_path / "snapshot.json"
    target.write_bytes(b"prior")
    real_stat = os.stat
    seen = 0

    def swap_before_second_stat(path, *args, **kwargs):
        nonlocal seen
        if path == source.name and kwargs.get("dir_fd") is not None:
            seen += 1
            if seen == 2:
                source.unlink()
                source.symlink_to(secret)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(durable_file.os, "stat", swap_before_second_stat)
    with pytest.raises(ValueError, match="DURABLE_REPLACE_UNSAFE_SOURCE"):
        durable_file.durable_replace(source, target)
    assert target.read_bytes() == b"prior"


@pytest.mark.parametrize("mode", [0o620, 0o602, 0o666])
def test_writable_source_never_replaces_snapshot(tmp_path, mode):
    source = tmp_path / ".new"
    target = tmp_path / "snapshot.json"
    target.write_bytes(b"prior")
    source.write_bytes(b"new")
    source.chmod(mode)
    with pytest.raises(ValueError, match="DURABLE_REPLACE_UNSAFE_SOURCE"):
        durable_file.durable_replace(source, target)
    assert target.read_bytes() == b"prior"
    assert source.read_bytes() == b"new"


def test_source_rewrite_with_restored_mtime_is_rejected_before_rename(tmp_path, monkeypatch):
    import os

    source = tmp_path / ".new"
    target = tmp_path / "snapshot.json"
    source.write_bytes(b"GOOD")
    target.write_bytes(b"prior")
    before = source.stat()
    actual_stat = os.stat
    seen = 0

    def adversarial_stat(path, *args, **kwargs):
        nonlocal seen
        if path == source.name and kwargs.get("dir_fd") is not None:
            seen += 1
            if seen == 2:
                source.write_bytes(b"EVIL")
                os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
        return actual_stat(path, *args, **kwargs)

    monkeypatch.setattr(durable_file.os, "stat", adversarial_stat)
    with pytest.raises(ValueError, match="DURABLE_REPLACE_SOURCE_CHANGED"):
        durable_file.durable_replace(source, target)
    assert target.read_bytes() == b"prior"
    assert source.read_bytes() == b"EVIL"
