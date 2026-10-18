"""Read-only triage of an absent PAPER unit; never an execution permit.

Host-wide absence cannot be inferred from a service name or from a pattern
search. In particular, renamed workers, user managers and inaccessible process
metadata need operator review. Results contain fixed codes and counts only;
process arguments and scheduler contents may contain secrets.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
import os
from pathlib import Path
import re
import stat
import time

PROPERTIES = ("LoadState", "ActiveState", "UnitFileState", "MainPID",
              "FragmentPath", "DropInPaths")
LIMIT = 4096
BYTE_LIMIT = 65536
PATTERN = re.compile(rb"hope|paper|/opt/hope", re.I)
SCHEDULES = ("/etc/crontab", "/etc/anacrontab", "/etc/cron.d",
    "/etc/cron.hourly", "/etc/cron.daily", "/etc/cron.weekly",
    "/etc/cron.monthly", "/var/spool/cron", "/var/spool/at",
    "/var/spool/atjobs", "/etc/systemd/user", "/run/systemd/user",
    "/root/.config/systemd/user", "/root/.local/share/systemd/user")


def unit_state(output):
    """Reject duplicate, missing and unexpected properties, even empty ones."""
    if not isinstance(output, str) or len(output) > BYTE_LIMIT:
        return "UNVERIFIED"
    fields = {}
    for line in output.splitlines():
        key, sep, value = line.partition("=")
        if not sep or key not in PROPERTIES or key in fields:
            return "UNVERIFIED"
        fields[key] = value
    if set(fields) != set(PROPERTIES):
        return "UNVERIFIED"
    if fields == dict(LoadState="not-found", ActiveState="inactive",
            UnitFileState="", MainPID="0", FragmentPath="", DropInPaths=""):
        return "ABSENT_REQUIRES_REVIEW"
    installed = (fields["LoadState"] == "loaded" and fields["FragmentPath"].startswith("/"))
    masked = (fields["LoadState"] == "masked" and fields["UnitFileState"] == "masked"
              and fields["FragmentPath"] == "/dev/null")
    if ((installed or masked) and fields["ActiveState"] == "inactive"
            and fields["UnitFileState"] in {"disabled", "masked"}
            and fields["MainPID"] == "0"
            and not fields["DropInPaths"]):
        return "INSTALLED_INACTIVE"
    return "UNSAFE"


def _read(path):
    # Do not follow schedule/process symlinks or read unbounded files.
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("UNREADABLE")
    with path.open("rb") as stream:
        result = stream.read(BYTE_LIMIT + 1)
    if len(result) > BYTE_LIMIT:
        raise ValueError("LIMIT")
    return result


def process_inventory(proc):
    count = candidates = 0
    deadline = time.monotonic() + 10
    for entry in proc.iterdir():
        if not entry.name.isdecimal():
            continue
        count += 1
        if count > LIMIT or time.monotonic() > deadline:
            raise ValueError("LIMIT")
        # Read identity on both sides: PID reuse/disappearance is ambiguous.
        before = _read(entry / "stat")
        command = _read(entry / "cmdline")
        cgroup = _read(entry / "cgroup")
        after = _read(entry / "stat")
        # CPU counters can change; field 22 (starttime) is the PID identity.
        def identity(value):
            tail = value.rsplit(b")", 1)
            if len(tail) != 2 or len(tail[1].split()) < 20:
                raise ValueError("IDENTITY")
            return tail[0].split(b"(", 1)[0], tail[1].split()[19]
        if identity(before) != identity(after) or identity(before)[0].strip() != entry.name.encode():
            raise ValueError("IDENTITY")
        if PATTERN.search(command + b"\n" + cgroup):
            candidates += 1
    if not count:
        raise ValueError("EMPTY_PROCESS_VIEW")
    return count, candidates


def schedule_inventory(paths):
    count = populated = 0
    deadline = time.monotonic() + 10
    pending = [(path, True) for path in paths]
    while pending:
        path, top = pending.pop()
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            # Missing top-level facilities are evidence, not a safety permit.
            if not top:
                raise ValueError("SCHEDULE_DISAPPEARED")
            continue
        count += 1
        if count > LIMIT or time.monotonic() > deadline:
            raise ValueError("LIMIT")
        if stat.S_ISDIR(mode):
            pending.extend((entry, False) for entry in path.iterdir())
        elif _read(path).strip():
            populated += 1
    return count, populated


def schedule_paths(home=Path("/home")):
    paths = [Path(p) for p in SCHEDULES]
    # Inspect user unit locations only, never recursively read home contents.
    for index, user in enumerate(home.iterdir()):
        if index >= LIMIT or not stat.S_ISDIR(user.lstat().st_mode):
            raise ValueError("HOME_INVENTORY")
        paths.extend((user/".config/systemd/user", user/".local/share/systemd/user"))
    return paths


@contextmanager
def deployment_view(root):
    """Probe the existing kernel guard; never create, replace or unlink it."""
    import fcntl
    path = root / ".activation.guard"
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o022 or info.st_nlink != 1):
            raise ValueError("GUARD_UNTRUSTED")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def systemd_inventory(run):
    """Inspect both loaded/transient and installed units, including aliases.

    All service commands are inspected, regardless of unit name. Unsupported
    JSON output, truncated output, or incomplete show responses are unverified.
    Timers/path/socket activation require review even when currently inactive.
    """
    names = set()
    deadline = time.monotonic() + 10
    schedules = 0
    for verb, key in (("list-units", "unit"), ("list-unit-files", "unit_file")):
        raw = run("systemctl", verb, "--all", "--no-pager", "--output=json")
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("LIMIT")
        rows = json.loads(raw)
        if not isinstance(rows, list) or not rows or len(rows) > LIMIT:
            raise ValueError("INVENTORY")
        for row in rows:
            name = row.get(key) if isinstance(row, dict) else None
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_@.:\\-]+", name):
                raise ValueError("UNIT")
            names.add(name)
    if len(names) > LIMIT:
        raise ValueError("LIMIT")
    candidates = 0
    for name in sorted(names):
        if time.monotonic() > deadline:
            raise ValueError("LIMIT")
        if name.endswith((".timer", ".path", ".socket")):
            schedules += 1
        if not name.endswith(".service"):
            continue
        raw = run("systemctl", "show", name,
            "--property=Id,LoadState,ActiveState,ExecStart,FragmentPath,DropInPaths")
        if len(raw) > BYTE_LIMIT:
            raise ValueError("LIMIT")
        fields = {}
        for line in raw.splitlines():
            key, sep, value = line.partition("=")
            if not sep or key in fields:
                raise ValueError("PROPERTIES")
            fields[key] = value
        if (set(fields) != {"Id", "LoadState", "ActiveState", "ExecStart", "FragmentPath", "DropInPaths"}
                or fields["Id"] != name or fields["LoadState"] != "loaded"
                or fields["ActiveState"] not in {"active", "inactive", "failed", "activating", "deactivating", "reloading"}):
            raise ValueError("PROPERTIES")
        if PATTERN.search(raw.encode()):
            candidates += 1
    return len(names), candidates, schedules


def audit(host, *, proc=Path("/proc"), schedules=None):
    """Bounded diagnostics only. An empty scan never authorizes activation."""
    result = dict(schema="hope.paper-absence-audit.v1", status="BLOCKED",
        execution_authorized=False, activation_authorized=False,
        required_review="HOST_EXECUTION_BOUNDARIES_AND_RESTART_ENFORCEMENT")
    checks = {}
    try:
        checks["canonical_unit"] = unit_state(host.paper_properties())
    except Exception:
        checks["canonical_unit"] = "UNVERIFIED"
    for key, inspect in (
            ("processes", lambda: process_inventory(proc)),
            ("systemd", lambda: systemd_inventory(host.run)),
            ("schedules", lambda: schedule_inventory(
                schedule_paths() if schedules is None else schedules)),
            ("deployment", lambda: host.absence_deployment_check())):
        try:
            counts = inspect()
            checks[key] = {"status": "REVIEW_REQUIRED", "counts": counts}
        except Exception:
            checks[key] = {"status": "UNVERIFIED"}
    result["checks"] = checks
    return result
