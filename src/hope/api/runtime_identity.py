"""Read-only interpreter/process identity; never execute an inspected runtime."""
from pathlib import Path
import os
import stat
import sys


def _require(value,code):
    if not value:raise RuntimeError("DASHBOARD_"+code)


def verify_interpreter(python):
    """A common base executable does not identify a virtual environment."""
    python=Path(python)
    _require(Path(sys.executable)==python and Path(sys.prefix)==python.parent.parent,
        "RUNTIME_IDENTITY_MISMATCH")


def _file_identity(path):
    info=path.stat()
    _require(stat.S_ISREG(info.st_mode),"LISTENER_RUNTIME_MISMATCH")
    return info.st_dev,info.st_ino,info.st_mode,info.st_size,info.st_mtime_ns,info.st_ctime_ns


def verify_listener(pid,python,*,identity,managed,expected_identity=None,proc=Path("/proc"),expected_arguments=None):
    """Bind one stable kernel process to the reviewed venv, module and port.

    Read only argv and executable metadata, never the process environment.
    The identity callback returns boot/start-tick identity, not PID alone.
    """
    _require(type(pid) is int and pid>0,"LISTENER_IDENTITY_UNKNOWN")
    python=Path(python);process=proc/str(pid);executable=process/"exe"
    before=identity(pid)
    _require(expected_identity is None or before==expected_identity,"LISTENER_PROCESS_CHANGED")
    target=python.resolve(strict=True)
    selected=_file_identity(python)
    _require(executable.resolve(strict=True)==target
        and _file_identity(executable)==selected,"LISTENER_RUNTIME_MISMATCH")
    with (process/"cmdline").open("rb") as stream:command=stream.read(65537)
    _require(0<len(command)<=65536 and command.endswith(b"\0"),"LISTENER_COMMAND_INVALID")
    arguments=command[:-1].split(b"\0")
    _require(arguments[0]==os.fsencode(python),"LISTENER_ENVIRONMENT_MISMATCH")
    expected=[os.fsencode(python)]
    if expected_arguments is not None:expected.extend(os.fsencode(value) for value in expected_arguments)
    else:
        if managed:expected.extend((b"-B",b"-s",b"-P"))
        expected.extend((b"-m",b"hope.api.dashboard_server",b"--port",b"8788"))
    _require(arguments[:len(expected)]==expected,"LISTENER_COMMAND_MISMATCH")
    _require(identity(pid)==before,"LISTENER_PROCESS_CHANGED")
    _require(python.resolve(strict=True)==target and _file_identity(python)==selected
        and _file_identity(executable)==selected,"LISTENER_RUNTIME_CHANGED")
