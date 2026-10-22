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
ARCHIVE_PREFIX = ".exporter-install-recovered-"
MAX_BYTES = 16384
NAMES = ("hope-ops-dashboard-export.service", "hope-ops-dashboard-export.timer")
TRANSITIONS = {
    "PREPARED": {"STAGED", "ABORTED"},
    "STAGED": {"PUBLISHING", "ABORTED"},
    "PUBLISHING": {"PUBLISHING", "RELOAD_PENDING", "ROLLBACK_PENDING", "RECOVERY_PENDING"},
    "RELOAD_PENDING": {"COMMITTED", "ROLLBACK_PENDING", "RECOVERY_PENDING"},
    "ROLLBACK_PENDING": {"ROLLED_BACK", "ROLLBACK_UNVERIFIED", "RECOVERY_PENDING"},
    "COMMITTED": set(), "ABORTED": set(), "ROLLED_BACK": set(),
    "ROLLBACK_UNVERIFIED": {"RECOVERY_PENDING"},
    "RECOVERY_PENDING": {"RECOVERED", "ROLLBACK_UNVERIFIED"},
    "RECOVERED": set(),
}


class JournalError(RuntimeError):
    pass


def validate_record(value):
    keys = {"version", "attempt", "sha", "unit_directory", "sequence", "phase", "units"}
    if not isinstance(value, dict) or set(value) != keys:
        raise JournalError("EXPORTER_JOURNAL_INVALID")
    if (type(value["version"]) is not int or value["version"] not in {1, 2}
            or not isinstance(value["attempt"], str) or not re.fullmatch(r"[0-9a-f]{32}", value["attempt"])
            or not isinstance(value["sha"], str) or not re.fullmatch(r"[0-9a-f]{40}", value["sha"])
            or not isinstance(value["unit_directory"], str) or not value["unit_directory"].startswith("/")
            or "\0" in value["unit_directory"]
            or type(value["sequence"]) is not int or not 0 <= value["sequence"] <= 32
            or not isinstance(value["phase"], str) or value["phase"] not in TRANSITIONS
            or not isinstance(value["units"], list) or len(value["units"]) > 2):
        raise JournalError("EXPORTER_JOURNAL_INVALID")
    seen = set()
    ordered_names = []
    publication_flags = []
    for unit in value["units"]:
        identity_keys = ({"name", "device", "inode", "sha256"}
                         if value["version"] == 1 else
                         {"name", "device", "inode", "size", "mtime_ns", "ctime_ns", "published", "sha256"})
        if (not isinstance(unit, dict) or set(unit) != identity_keys
                or unit["name"] not in NAMES or unit["name"] in seen
                or type(unit["device"]) is not int or unit["device"] < 0
                or type(unit["inode"]) is not int or unit["inode"] <= 0
                or (value["version"] == 2 and
                    (type(unit["size"]) is not int or unit["size"] < 0
                     or type(unit["mtime_ns"]) is not int or unit["mtime_ns"] < 0
                     or type(unit["ctime_ns"]) is not int or unit["ctime_ns"] < 0
                     or type(unit["published"]) is not bool))
                or not isinstance(unit["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", unit["sha256"])):
            raise JournalError("EXPORTER_JOURNAL_INVALID")
        seen.add(unit["name"])
        ordered_names.append(unit["name"])
        if value["version"] == 2:
            publication_flags.append(unit["published"])
    if ordered_names != list(NAMES[:len(ordered_names)]):
        raise JournalError("EXPORTER_JOURNAL_INVALID")
    if (value["phase"] == "PUBLISHING" and not ordered_names
            or value["version"] == 2
            and publication_flags not in (
                [True] * len(publication_flags),
                [True] * (len(publication_flags) - 1) + [False],
            )
            or value["version"] == 2
            and value["phase"] != "PUBLISHING"
            and not all(publication_flags)):
        raise JournalError("EXPORTER_JOURNAL_INVALID")
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


def _private(info, owner, *, links=(1,)):
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink not in links
            or info.st_uid != owner or info.st_mode & 0o077):
        raise JournalError("EXPORTER_JOURNAL_UNSAFE")


def _flags():
    if os.name != "posix":
        raise JournalError("EXPORTER_JOURNAL_UNSUPPORTED")
    return os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


def _read_named_at(root_fd, name, owner, *, links=(1,)):
    fd = os.open(name, os.O_RDONLY | _flags(), dir_fd=root_fd)
    try:
        before = os.fstat(fd)
        _private(before, owner, links=links)
        payload = bytearray()
        while len(payload) <= MAX_BYTES:
            block = os.read(fd, MAX_BYTES + 1 - len(payload))
            if not block:
                break
            payload.extend(block)
        after = os.fstat(fd)
        named = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        _private(named, owner, links=links)
        if _identity(before) != _identity(after) or _identity(after) != _identity(named):
            raise JournalError("EXPORTER_JOURNAL_CHANGED")
        return decode_record(payload), (named.st_dev, named.st_ino)
    finally:
        os.close(fd)


def _read_at(root_fd, owner):
    return _read_named_at(root_fd, JOURNAL, owner)


def _archive_name(attempt):
    if not isinstance(attempt, str) or not re.fullmatch(r"[0-9a-f]{32}", attempt):
        raise JournalError("EXPORTER_FINALIZATION_EVIDENCE_MISMATCH")
    return ARCHIVE_PREFIX + attempt + ".json"


class InstallJournal:
    def __init__(self, root_fd, root, expected, unit_dir, *, owner_uid=0):
        self.root_fd, self.root, self.owner = root_fd, Path(root), owner_uid
        self.guard = -1
        self.active = False
        self.healthy = True
        self.named = None
        self.data = dict(version=2, attempt=secrets.token_hex(16), sha=expected,
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
        units = []
        for entry in installed:
            name, identity, digest = entry[:3]
            published = entry[3] if len(entry) == 4 else True
            units.append(dict(name=name, device=identity[0], inode=identity[1],
                              size=identity[2], mtime_ns=identity[3],
                              ctime_ns=identity[4], published=published,
                              sha256=digest))
        self._write(dict(self.data, phase=phase, sequence=self.data["sequence"] + 1, units=units))

    @classmethod
    def open_recovery(cls, root_fd, root, expected, unit_dir, attempt, *, owner_uid=0):
        """Exclusively reopen exact v2 evidence; never infer recovery authority."""
        import fcntl
        journal = cls(root_fd, root, expected, unit_dir, owner_uid=owner_uid)
        journal._root()
        try:
            journal.guard = os.open(GUARD, os.O_RDWR | _flags(), dir_fd=root_fd)
            guard = os.fstat(journal.guard)
            _private(guard, owner_uid)
            named_guard = os.stat(GUARD, dir_fd=root_fd, follow_symlinks=False)
            if _identity(guard) != _identity(named_guard):
                raise JournalError("EXPORTER_JOURNAL_GUARD_CHANGED")
            try:
                fcntl.flock(journal.guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise JournalError("EXPORTER_INSTALL_CONCURRENT") from None
            value, identity = _read_at(root_fd, owner_uid)
            if (value["version"] != 2 or value["sha"] != expected
                    or value["unit_directory"] != str(unit_dir)
                    or value["attempt"] != attempt
                    or value["phase"] not in {
                        "PUBLISHING", "RELOAD_PENDING", "ROLLBACK_PENDING",
                        "ROLLBACK_UNVERIFIED", "RECOVERY_PENDING",
                    }
                    or not value["units"]):
                raise JournalError("EXPORTER_RECOVERY_EVIDENCE_MISMATCH")
            journal.data = value
            journal.named = identity
            journal.active = True
            journal.healthy = True
            return journal
        except BaseException:
            journal.close()
            raise

    @classmethod
    def open_finalization(cls, root_fd, root, expected, unit_dir, attempt,
                          *, owner_uid=0):
        """Lock exact terminal recovery evidence, including interrupted archive."""
        import fcntl
        journal = cls(root_fd, root, expected, unit_dir, owner_uid=owner_uid)
        journal._root()
        archive = _archive_name(attempt)
        try:
            journal.guard = os.open(GUARD, os.O_RDWR | _flags(), dir_fd=root_fd)
            guard = os.fstat(journal.guard)
            _private(guard, owner_uid)
            named_guard = os.stat(GUARD, dir_fd=root_fd, follow_symlinks=False)
            if _identity(guard) != _identity(named_guard):
                raise JournalError("EXPORTER_JOURNAL_GUARD_CHANGED")
            try:
                fcntl.flock(journal.guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise JournalError("EXPORTER_INSTALL_CONCURRENT") from None

            try:
                os.stat(JOURNAL, dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError:
                value, _ = _read_named_at(root_fd, archive, owner_uid)
                identity = None
            else:
                try:
                    archived = os.stat(
                        archive, dir_fd=root_fd, follow_symlinks=False
                    )
                except FileNotFoundError:
                    value, identity = _read_at(root_fd, owner_uid)
                else:
                    value, identity = _read_named_at(
                        root_fd, JOURNAL, owner_uid, links=(2,)
                    )
                    archived_value, archived_identity = _read_named_at(
                        root_fd, archive, owner_uid, links=(2,)
                    )
                    if (archived_value != value
                            or archived_identity != identity
                            or _identity(archived)[:2] != identity):
                        raise JournalError("EXPORTER_FINALIZATION_ARCHIVE_CONFLICT")
            if (value["version"] != 2 or value["sha"] != expected
                    or value["unit_directory"] != str(unit_dir)
                    or value["attempt"] != attempt
                    or value["phase"] != "RECOVERED" or not value["units"]):
                raise JournalError("EXPORTER_FINALIZATION_EVIDENCE_MISMATCH")
            journal.data = value
            journal.named = identity
            journal.active = True
            journal.healthy = True
            return journal
        except BaseException:
            journal.close()
            raise

    def archive_recovered(self):
        """Durably retain exact terminal evidence, then clear only its live name."""
        if (not self.active or not self.healthy
                or self.data["phase"] != "RECOVERED"):
            raise JournalError("EXPORTER_FINALIZATION_EVIDENCE_MISMATCH")
        archive = _archive_name(self.data["attempt"])
        self._root()
        guard = os.fstat(self.guard)
        named_guard = os.stat(GUARD, dir_fd=self.root_fd, follow_symlinks=False)
        _private(named_guard, self.owner)
        if _identity(guard) != _identity(named_guard):
            raise JournalError("EXPORTER_JOURNAL_GUARD_CHANGED")

        if self.named is None:
            archived, _ = _read_named_at(self.root_fd, archive, self.owner)
            if archived != self.data:
                raise JournalError("EXPORTER_FINALIZATION_ARCHIVE_CONFLICT")
            return archive

        try:
            os.stat(archive, dir_fd=self.root_fd, follow_symlinks=False)
        except FileNotFoundError:
            current, identity = _read_at(self.root_fd, self.owner)
            if current != self.data or identity != self.named:
                raise JournalError("EXPORTER_JOURNAL_CHANGED")
            os.link(JOURNAL, archive, src_dir_fd=self.root_fd,
                    dst_dir_fd=self.root_fd, follow_symlinks=False)
            os.fsync(self.root_fd)

        current, identity = _read_named_at(
            self.root_fd, JOURNAL, self.owner, links=(2,)
        )
        archived, archived_identity = _read_named_at(
            self.root_fd, archive, self.owner, links=(2,)
        )
        if (current != self.data or archived != self.data
                or identity != archived_identity):
            raise JournalError("EXPORTER_FINALIZATION_ARCHIVE_CONFLICT")
        self._root()
        os.unlink(JOURNAL, dir_fd=self.root_fd)
        os.fsync(self.root_fd)
        self.named = None
        return archive

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
