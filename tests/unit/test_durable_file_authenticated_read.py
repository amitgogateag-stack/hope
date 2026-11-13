"""R2: bounded reads must not treat inode metadata as a content signature."""
import hashlib
import os
from pathlib import Path

import pytest

from hope.infrastructure.durable_file import (
    authenticated_bounded_regular_read,
    bounded_regular_read,
)


def test_authenticated_bounded_read_accepts_exact_digest(tmp_path):
    path = tmp_path / "snapshot"
    payload = b"known trusted bytes"
    path.write_bytes(payload)
    assert authenticated_bounded_regular_read(
        path, 1024, expected_sha256=hashlib.sha256(payload).hexdigest()
    ) == payload


def test_authenticated_bounded_read_rejects_same_size_modified_bytes(tmp_path):
    path = tmp_path / "snapshot"
    original = b"original-record"
    modified = b"modified-record"
    assert len(original) == len(modified)
    path.write_bytes(original)
    expected = hashlib.sha256(original).hexdigest()
    before = path.stat()
    path.write_bytes(modified)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(ValueError, match="BOUNDED_READ_DIGEST_MISMATCH"):
        authenticated_bounded_regular_read(path, 1024, expected_sha256=expected)


@pytest.mark.parametrize("bad", ["", "0" * 63, "G" * 64, "a" * 65, 123, None])
def test_authenticated_bounded_read_digest_validation(tmp_path, bad):
    path = tmp_path / "snapshot"
    path.write_bytes(b"ok")
    if bad is None:
        assert bounded_regular_read(path, 2) == b"ok"
    else:
        with pytest.raises(ValueError, match="BOUNDED_READ_INVALID_DIGEST"):
            authenticated_bounded_regular_read(path, 2, expected_sha256=bad)


def test_wrong_trusted_digest_fails_even_with_unchanged_metadata(tmp_path):
    path = tmp_path / "snapshot"
    path.write_bytes(b"stable")
    with pytest.raises(ValueError, match="BOUNDED_READ_DIGEST_MISMATCH"):
        authenticated_bounded_regular_read(path, 1024, expected_sha256="0" * 64)


def test_authenticated_read_rejects_mutation_during_descriptor_read(tmp_path, monkeypatch):
    """Mutation after descriptor pinning cannot bypass trusted content digest."""
    path = tmp_path / "snapshot"
    original = b"A" * 128
    modified = b"B" * 128
    path.write_bytes(original)
    expected = hashlib.sha256(original).hexdigest()
    real_read = os.read
    modified_once = False

    def mutate_before_read(fd, count):
        nonlocal modified_once
        if not modified_once:
            modified_once = True
            before = path.stat()
            with path.open("r+b") as stream:
                stream.write(modified)
                stream.flush()
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        return real_read(fd, count)

    monkeypatch.setattr(os, "read", mutate_before_read)
    with pytest.raises(ValueError, match="BOUNDED_READ_(FILE_CHANGED|DIGEST_MISMATCH)"):
        authenticated_bounded_regular_read(path, 1024, expected_sha256=expected)
    assert modified_once


def test_authenticated_reader_cannot_silently_downgrade_to_metadata_only(tmp_path):
    path = tmp_path / "snapshot"
    path.write_bytes(b"attacker-controlled")

    with pytest.raises(ValueError, match="BOUNDED_READ_INVALID_DIGEST"):
        authenticated_bounded_regular_read(path, 1024, expected_sha256=None)
    with pytest.raises(TypeError):
        bounded_regular_read(path, 1024, expected_sha256=None)


def test_authenticated_reader_rejects_digest_object_with_forged_equality(tmp_path):
    """A string-like object cannot override digest equality and authenticate bytes."""
    path = tmp_path / "snapshot"
    payload = b"attacker-controlled"
    path.write_bytes(payload)

    class ForgedDigest(str):
        def __eq__(self, other):
            return True

        def __ne__(self, other):
            return False

    forged = ForgedDigest("0" * 64)
    assert str(forged) != hashlib.sha256(payload).hexdigest()
    with pytest.raises(ValueError, match="BOUNDED_READ_INVALID_DIGEST"):
        authenticated_bounded_regular_read(path, 1024, expected_sha256=forged)
