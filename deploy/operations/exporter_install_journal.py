"""Private write-ahead evidence for exporter installation; never recovery authority."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
import stat

JOURNAL = ".exporter-install.json"
GUARD = ".exporter-install.guard"
MAX_BYTES = 16384
NAMES = ("hope-ops-dashboard-export.service", "hope-ops-dashboard-export.timer")
TRANSITIONS = {
    "PREPARED": {"STAGED", "ABORTED"},
    "STAGED": {"PUBLISHING", "ABORTED"},
    "PUBLISHING": {"PUBLISHING", "RELOAD_PENDING", "ROLLBACK_PENDING"},
    "RELOAD_PENDING": {"COMMITTED", "ROLLBACK_PENDING"},
    "ROLLBACK_PENDING": {"ROLLED_BACK", "ROLLBACK_UNVERIFIED"},
    "COMMITTED": set(), "ABORTED": set(), "ROLLED_BACK": set(),
    "ROLLBACK_UNVERIFIED": set(),
}


class JournalError(RuntimeError):
    pass


def validate_record(value):
    keys = {"version", "attempt", "sha", "unit_directory", "sequence", "phase", "units"}
    if not isinstance(value, dict) or set(value) != keys:
        raise JournalError("EXPORTER_JOURNAL_INVALID")
    if (type(value["version"]) is not int or value["version"] != 1
            or not isinstance(value["attempt"], str) or not re.fullmatch(r"[0-9a-f]{32}", value["attempt"])
            or not isinstance(value["sha"], str) or not re.fullmatch(r"[0-9a-f]{40}", value["sha"])
            or not isinstance(value["unit_directory"], str) or not value["unit_directory"].startswith("/")
            or "\0" in value["unit_directory"]
            or type(value["sequence"]) is not int or not 0 <= value["sequence"] <= 32
            or not isinstance(value["phase"], str) or value["phase"] not in TRANSITIONS
            or not isinstance(value["units"], list) or len(value["units"]) > 2):
        raise JournalError("EXPORTER_JOURNAL_INVALID")
    seen = set()
    for unit in value["units"]:
        if (not isinstance(unit, dict) or set(unit) != {"name", "device", "inode", "sha256"}
                or unit["name"] not in NAMES or unit["name"] in seen
                or type(unit["device"]) is not int or unit["device"] < 0
                or type(unit["inode"]) is not int or unit["inode"] <= 0
                or not isinstance(unit["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", unit["sha256"])):
            raise JournalError("EXPORTER_JOURNAL_INVALID")
        seen.add(unit["name"])
    if value["phase"] in {"RELOAD_PENDING", "COMMITTED"} and seen != set(NAMES):
        raise JournalError("EXPORTER_JOURNAL_INVALID")
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise JournalError("EXPORTER_JOURNAL_INVALID")
        result[key] = value
    return result


def decode_record(payload):
    if not payload or len(payload) > MAX_BYTES:
        raise JournalError("EXPORTER_JOURNAL_INVALID")
    try:
        return validate_record(json.loads(payload, object_pairs_hook=_pairs))
    except (ValueError, UnicodeError, TypeError, KeyError):
        raise JournalError("EXPORTER_JOURNAL_INVALID") from None


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _private(info, owner):
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != owner or info.st_mode & 0o077):
        raise JournalError("EXPORTER_JOURNAL_UNSAFE")


def _flags():
    if os.name != "posix":
        raise JournalError("EXPORTER_JOURNAL_UNSUPPORTED")
    return os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


def _read_at(root_fd, owner):
    fd = os.open(JOURNAL, os.O_RDONLY | _flags(), dir_fd=root_fd)
    try:
        before = os.fstat(fd)
        _private(before, owner)
        payload = bytearray()
        while len(payload) <= MAX_BYTES:
            block = os.read(fd, MAX_BYTES + 1 - len(payload))
            if not block:
                break
            payload.extend(block)
        after = os.fstat(fd)
        named = os.stat(JOURNAL, dir_fd=root_fd, follow_symlinks=False)
        _private(named, owner)
        if _identity(before) != _identity(after) or _identity(after) != _identity(named):
            raise JournalError("EXPORTER_JOURNAL_CHANGED")
        return decode_record(payload), (named.st_dev, named.st_ino)
    finally:
        os.close(fd)


class InstallJournal:
    def __init__(self, root_fd, root, expected, unit_dir, *, owner_uid=0):
        self.root_fd, self.root, self.owner = root_fd, Path(root), owner_uid
        self.guard = -1
        self.active = False
        self.healthy = True
        self.named = None
        self.data = dict(version=1, attempt=secrets.token_hex(16), sha=expected,
                         unit_directory=str(unit_dir), sequence=0, phase="PREPARED", units=[])

    def _root(self):
        opened, named = os.fstat(self.root_fd), os.stat(self.root, follow_symlinks=False)
        if (not stat.S_ISDIR(named.st_mode) or named.st_uid != self.owner or named.st_mode & 0o022
                or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)):
            raise JournalError("EXPORTER_JOURNAL_ROOT_CHANGED")

    def start(self):
        import fcntl
        self._root()
        try:
            self.guard = os.open(GUARD, os.O_RDWR | os.O_CREAT | _flags(), 0o600, dir_fd=self.root_fd)
            info = os.fstat(self.guard)
            _private(info, self.owner)
            named = os.stat(GUARD, dir_fd=self.root_fd, follow_symlinks=False)
            if _identity(info) != _identity(named):
                raise JournalError("EXPORTER_JOURNAL_GUARD_CHANGED")
            try:
                fcntl.flock(self.guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise JournalError("EXPORTER_INSTALL_CONCURRENT") from None
            try:
                os.stat(JOURNAL, dir_fd=self.root_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise JournalError("EXPORTER_INSTALL_RECOVERY_REQUIRED")
            self._write(self.data)
            self.active = True
            return self
        except BaseException:
            self.close()
            raise

    def _write(self, value):
        self._root()
        guard = os.fstat(self.guard)
        named_guard = os.stat(GUARD, dir_fd=self.root_fd, follow_symlinks=False)
        _private(named_guard, self.owner)
        if _identity(guard) != _identity(named_guard):
            raise JournalError("EXPORTER_JOURNAL_GUARD_CHANGED")
        validate_record(value)
        payload = (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
        if len(payload) > MAX_BYTES:
            raise JournalError("EXPORTER_JOURNAL_INVALID")
        if self.named is not None:
            previous, identity = _read_at(self.root_fd, self.owner)
            if identity != self.named or previous != self.data:
                raise JournalError("EXPORTER_JOURNAL_CHANGED")
        temporary = ".exporter-journal-" + secrets.token_hex(16) + ".tmp"
        fd = -1
        self.healthy = False
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _flags(), 0o600, dir_fd=self.root_fd)
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise JournalError("EXPORTER_JOURNAL_WRITE_FAILED")
                view = view[written:]
            os.fsync(fd)
            info = os.fstat(fd)
            _private(info, self.owner)
            if self.named is None:
                # First publication must never overwrite evidence, even from
                # a non-cooperating privileged writer.
                os.link(temporary, JOURNAL, src_dir_fd=self.root_fd,
                        dst_dir_fd=self.root_fd, follow_symlinks=False)
                os.unlink(temporary, dir_fd=self.root_fd)
            else:
                os.replace(temporary, JOURNAL, src_dir_fd=self.root_fd, dst_dir_fd=self.root_fd)
            os.fsync(self.root_fd)
            self._root()
            self.named = info.st_dev, info.st_ino
            self.data = value
            self.healthy = True
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(temporary, dir_fd=self.root_fd)
            except FileNotFoundError:
                pass

    def record(self, phase, installed):
        if not self.active or not self.healthy or phase not in TRANSITIONS[self.data["phase"]]:
            raise JournalError("EXPORTER_JOURNAL_TRANSITION_INVALID")
        units = [dict(name=name, device=identity[0], inode=identity[1], sha256=digest)
                 for name, identity, digest in installed]
        self._write(dict(self.data, phase=phase, sequence=self.data["sequence"] + 1, units=units))

    def close(self):
        if self.guard >= 0:
            os.close(self.guard)
            self.guard = -1


def inspect_journal(root, *, owner_uid=0):
    """Read existing evidence without creating files or authorizing recovery."""
    import fcntl
    root = Path(root)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | _flags())
    guard = -1
    try:
        probe = InstallJournal(root_fd, root, "0" * 40, Path("/unused"), owner_uid=owner_uid)
        probe._root()
        guard = os.open(GUARD, os.O_RDONLY | _flags(), dir_fd=root_fd)
        before = os.fstat(guard)
        _private(before, owner_uid)
        fcntl.flock(guard, fcntl.LOCK_SH | fcntl.LOCK_NB)
        named = os.stat(GUARD, dir_fd=root_fd, follow_symlinks=False)
        if _identity(before) != _identity(named):
            raise JournalError("EXPORTER_JOURNAL_GUARD_CHANGED")
        value, _ = _read_at(root_fd, owner_uid)
        probe._root()
        return value
    finally:
        if guard >= 0:
            os.close(guard)
        os.close(root_fd)
