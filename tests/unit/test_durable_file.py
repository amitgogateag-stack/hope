"""Crash-durability contract for operational file replacement."""
from pathlib import Path

import pytest

from hope.infrastructure import durable_file


def test_replace_opens_directory_before_rename_and_syncs_after(monkeypatch, tmp_path):
    source = tmp_path / ".snapshot.tmp"
    target = tmp_path / "snapshot.json"
    source.write_bytes(b"complete")
    calls = []

    monkeypatch.setattr(
        durable_file.os,
        "open",
        lambda path, flags: calls.append(("open", Path(path), flags)) or 71,
    )
    monkeypatch.setattr(
        durable_file.os,
        "replace",
        lambda old, new: calls.append(("replace", Path(old), Path(new))),
    )
    monkeypatch.setattr(durable_file.os, "fsync", lambda descriptor: calls.append(("fsync", descriptor)))
    monkeypatch.setattr(durable_file.os, "close", lambda descriptor: calls.append(("close", descriptor)))

    durable_file.durable_replace(source, target)

    assert [call[0] for call in calls] == ["open", "replace", "fsync", "close"]
    assert calls[0][1] == tmp_path
    assert calls[1][1:] == (source, target)
    assert calls[2:] == [("fsync", 71), ("close", 71)]


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
    closed = []
    monkeypatch.setattr(durable_file.os, "open", lambda *_: 72)

    def replace(*_):
        if failure == "replace":
            raise OSError("replace failed")

    def fsync(*_):
        if failure == "fsync":
            raise OSError("fsync failed")

    monkeypatch.setattr(durable_file.os, "replace", replace)
    monkeypatch.setattr(durable_file.os, "fsync", fsync)
    monkeypatch.setattr(durable_file.os, "close", closed.append)

    with pytest.raises(OSError, match=f"{failure} failed"):
        durable_file.durable_replace(source, target)
    assert closed == [72]


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
