"""Crash-durable publication primitives for private operational files."""
from __future__ import annotations

import os
from pathlib import Path


def durable_replace(source: str | Path, target: str | Path) -> None:
    """Replace a synced file and persist its containing-directory entry."""
    source_path = Path(source)
    target_path = Path(target)
    if not os.path.samefile(source_path.parent, target_path.parent):
        raise ValueError("DURABLE_REPLACE_DIRECTORY_MISMATCH")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    # Open before replacement so unsupported directory syncing leaves the prior
    # target intact instead of publishing an unacknowledged durability state.
    directory = os.open(target_path.parent, flags)
    try:
        os.replace(source_path, target_path)
        os.fsync(directory)
    finally:
        os.close(directory)
