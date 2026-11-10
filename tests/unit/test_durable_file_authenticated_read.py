"""R2: bounded reads must not treat inode metadata as a content signature."""
import hashlib
import os
from pathlib import Path

import pytest

from hope.infrastructure.durable_file import bounded_regular_read


def test_authenticated_bounded_read_accepts_exact_digest(tmp_path):
    path = tmp_path / "snapshot"
    payload = b"known trusted bytes"
    path.write_bytes(payload)
    assert bounded_regular_read(path, 1024, expected_sha256=hashlib.sha256(payload).hexdigest()) == payload


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
        bounded_regular_read(path, 1024, expected_sha256=expected)


@pytest.mark.parametrize("bad", ["", "0" * 63, "G" * 64, "a" * 65, 123, None])
def test_authenticated_bounded_read_digest_validation(tmp_path, bad):
    path = tmp_path / "snapshot"
    path.write_bytes(b"ok")
    if bad is None:
        assert bounded_regular_read(path, 2) == b"ok"
    else:
        with pytest.raises(ValueError, match="BOUNDED_READ_INVALID_DIGEST"):
            bounded_regular_read(path, 2, expected_sha256=bad)


def test_wrong_trusted_digest_fails_even_with_unchanged_metadata(tmp_path):
    path = tmp_path / "snapshot"
    path.write_bytes(b"stable")
    with pytest.raises(ValueError, match="BOUNDED_READ_DIGEST_MISMATCH"):
        bounded_regular_read(path, 1024, expected_sha256="0" * 64)
