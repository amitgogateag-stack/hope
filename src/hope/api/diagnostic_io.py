"""Bounded POSIX child output for read-only host diagnostics.

Never invoke a shell, inherit credentials, or retain stderr. Output remains
private to the caller, which publishes fixed diagnostic codes only.
"""
import os
import selectors
import signal
import subprocess
import time


def bounded_capture(arguments, *, timeout, max_output, env):
    if os.name != "posix":
        raise RuntimeError("DIAGNOSTIC_POSIX_REQUIRED")
    if not 0 < timeout <= 20 or not 0 < max_output <= 4 * 1024 * 1024:
        raise ValueError("DIAGNOSTIC_BOUNDS_INVALID")
    deadline = time.monotonic() + timeout
    child = subprocess.Popen(arguments, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
        close_fds=True, start_new_session=True)
    reaped = False
    try:
        output = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("DIAGNOSTIC_TIMEOUT")
                if not selector.select(remaining):
                    raise RuntimeError("DIAGNOSTIC_TIMEOUT")
                block = os.read(child.stdout.fileno(), min(65536, max_output + 1 - len(output)))
                if not block:
                    selector.unregister(child.stdout)
                else:
                    output.extend(block)
                    if len(output) > max_output:
                        raise RuntimeError("DIAGNOSTIC_OUTPUT_LIMIT")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("DIAGNOSTIC_TIMEOUT")
        try:
            code = child.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            raise RuntimeError("DIAGNOSTIC_TIMEOUT") from None
        reaped = True
        value = output.decode("utf-8")
        if code:
            raise subprocess.CalledProcessError(code, arguments, output=value)
        return value
    finally:
        try:
            if not reaped and child.returncode is None:
                # No poll/wait has reaped the session leader: its PID cannot be
                # reused while killing descendants that retain stdout. A wait
                # interrupted after reaping may already set returncode.
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait(timeout=1)
        finally:
            child.stdout.close()
