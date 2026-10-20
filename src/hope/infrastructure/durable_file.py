"""Crash-durable publication primitives for private operational files."""
from __future__ import annotations

import os
import stat
from pathlib import Path


def durable_replace(source: str | Path, target: str | Path) -> None:
    """Publish a verified regular file inside a pinned, synced directory.

    The directory descriptor prevents pathname redirection. Reject source
    symlinks, non-files, hardlinks and group/world-writable source files;
    detect source substitution and metadata changes before and after rename.
    Publication directories must not be attacker-writable: a hostile writer
    can race the final check and rename.
    """
    source_path = Path(source)
    target_path = Path(target)
    if source_path.name == target_path.name:
        raise ValueError("DURABLE_REPLACE_IDENTICAL_PATH")
    if not os.path.samefile(source_path.parent, target_path.parent):
        raise ValueError("DURABLE_REPLACE_DIRECTORY_MISMATCH")
    flags = (os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    # Opening before replacement also preserves the previous target if the
    # platform cannot open a directory for durable fsync.
    directory = os.open(target_path.parent, flags)
    try:
        opened = os.fstat(directory)

        def check_parent(path: Path) -> None:
            named = os.stat(path, follow_symlinks=False)
            if (not stat.S_ISDIR(named.st_mode)
                    or (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino)):
                raise ValueError("DURABLE_REPLACE_DIRECTORY_CHANGED")

        def source_identity() -> tuple[int, int, int, int, int]:
            info = os.stat(source_path.name, dir_fd=directory, follow_symlinks=False)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_mode & 0o022):
                raise ValueError("DURABLE_REPLACE_UNSAFE_SOURCE")
            # Size and mtime alone do not authenticate an inode's contents:
            # an in-place rewrite can restore mtime while changing ctime.
            return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

        check_parent(source_path.parent)
        check_parent(target_path.parent)
        expected = source_identity()
        if source_identity() != expected:
            raise ValueError("DURABLE_REPLACE_SOURCE_CHANGED")
        os.replace(source_path.name, target_path.name,
                   src_dir_fd=directory, dst_dir_fd=directory)
        published = os.stat(target_path.name, dir_fd=directory, follow_symlinks=False)
        # Rename itself updates ctime on Linux, so compare the pre-rename
        # ctime in the two preflight observations, not against the renamed file.
        if (not stat.S_ISREG(published.st_mode) or published.st_nlink != 1
                or published.st_mode & 0o022
                or (published.st_dev, published.st_ino, published.st_size,
                    published.st_mtime_ns) != expected[:4]):
            raise ValueError("DURABLE_REPLACE_SOURCE_CHANGED")
        os.fsync(directory)
        check_parent(target_path.parent)
    finally:
        os.close(directory)
