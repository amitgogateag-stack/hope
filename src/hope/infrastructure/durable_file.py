"""Crash-durable publication primitives for private operational files."""
from __future__ import annotations

import os
import stat
from pathlib import Path


def durable_replace(source: str | Path, target: str | Path) -> None:
    """Publish only inside a pinned directory and sync that exact directory.

    A pathname-based rename can be redirected if the containing directory is
    replaced after inspection. Both rename operands therefore use the opened
    directory descriptor. Refuse an untrusted alias or a changed directory path
    instead of reporting successful publication to a different directory.
    """
    source_path = Path(source)
    target_path = Path(target)
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

        check_parent(source_path.parent)
        check_parent(target_path.parent)
        os.replace(source_path.name, target_path.name,
                   src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
        check_parent(target_path.parent)
    finally:
        os.close(directory)
