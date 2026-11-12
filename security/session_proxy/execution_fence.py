"""Linux-only, single-threaded prototype execution fence; NOT an attestor.

Kernel ptrace events, not /proc byte equality, invalidate an attached execution
lease. The tracee is stopped through PostgreSQL COMMIT. Existing loaded code,
pre-attachment history and production image provenance remain unproven.
"""
from contextlib import contextmanager
import ctypes
import os
from pathlib import Path
import select
import signal
import sys
import threading
import time

SEIZE, INTERRUPT, CONT, DETACH, GETEVENTMSG = 0x4206, 0x4207, 7, 17, 0x4201
TRACE_FORK, TRACE_VFORK, TRACE_CLONE, TRACE_EXEC, EXITKILL = 2, 4, 8, 16, 0x100000
EVENT_EXEC, EVENT_STOP = 4, 128
WALL = 0x40000000


class FenceDenied(Exception):
    pass


class ExecutionFence:
    def __init__(self, pid):
        if sys.platform != 'linux' or type(pid) is not int or pid <= 0 or pid == os.getpid():
            raise FenceDenied('EXEC_FENCE_UNSUPPORTED_TARGET')
        self.pid, self.tid = pid, threading.get_native_id()
        self.pidfd = os.pidfd_open(pid)
        self.attached = self.paused = False
        self.violation = None
        self.events = []
        self.children = set()
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.ptrace.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
        self.libc.ptrace.restype = ctypes.c_long
        try:
            self._call(SEIZE, TRACE_FORK | TRACE_VFORK | TRACE_CLONE | TRACE_EXEC | EXITKILL)
            self.attached = True
            self._pause()
            if {int(p.name) for p in (Path('/proc') / str(pid) / 'task').iterdir()} != {pid}:
                self._deny('EXEC_FENCE_MULTITHREADED_TARGET')
        except BaseException:
            self.close()
            raise

    def _call(self, command, data=0):
        if threading.get_native_id() != self.tid:
            raise FenceDenied('EXEC_FENCE_TRACER_THREAD_CHANGED')
        if self.libc.ptrace(command, self.pid, None, ctypes.c_void_p(data)) == -1:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))

    def _deny(self, reason):
        self.violation = reason
        raise FenceDenied(reason)

    def _status(self, status, expected_stop=False):
        if not os.WIFSTOPPED(status):
            self._deny('EXEC_FENCE_PROCESS_EXITED')
        self.paused = True
        event = status >> 16
        self.events.append({'event': event, 'signal': os.WSTOPSIG(status)})
        if event == EVENT_EXEC:
            self._deny('EXEC_FENCE_EXEC_EVENT')
        if event in {1, 2, 3}:
            child = ctypes.c_ulong()
            self._call(GETEVENTMSG, ctypes.addressof(child))
            self.children.add(child.value)
            self._deny('EXEC_FENCE_TASK_CREATION')
        if not (expected_stop and event == EVENT_STOP and os.WSTOPSIG(status) == signal.SIGTRAP):
            self._deny('EXEC_FENCE_UNEXPECTED_STOP')

    def check(self):
        if self.violation:
            raise FenceDenied(self.violation)
        if self.paused:
            return
        target, status = os.waitpid(self.pid, os.WNOHANG | WALL)
        if target:
            self._status(status)
        if select.select([self.pidfd], [], [], 0)[0]:
            self._deny('EXEC_FENCE_PROCESS_EXITED')

    def _pause(self):
        self.check()
        if self.paused:
            return
        self._call(INTERRUPT)
        until = time.monotonic() + 2
        while time.monotonic() < until:
            target, status = os.waitpid(self.pid, os.WNOHANG | WALL)
            if target:
                self._status(status, expected_stop=True)
                return
            time.sleep(.001)
        self._deny('EXEC_FENCE_STOP_TIMEOUT')

    def resume(self):
        if not self.paused or self.violation:
            raise FenceDenied('EXEC_FENCE_RESUME_INVALID')
        self._call(CONT)
        self.paused = False

    @contextmanager
    def stopped(self):
        self._pause()
        try:
            yield
        finally:
            # Failures keep the peer stopped until close() kills it. Never
            # silently resume an invalidated lease after COMMIT rejection.
            if sys.exc_info()[0] is None and not self.violation:
                self.resume()

    def close(self):
        if self.pidfd is None:
            return
        try:
            if self.attached:
                try:
                    if self.violation:
                        signal.pidfd_send_signal(self.pidfd, signal.SIGKILL)
                        for child in self.children:
                            try:
                                fd = os.pidfd_open(child)
                                try: signal.pidfd_send_signal(fd, signal.SIGKILL)
                                finally: os.close(fd)
                            except OSError:
                                pass
                    else:
                        self._pause()
                        self._call(DETACH)
                except (OSError, ChildProcessError, FenceDenied):
                    # A failed detach is never acceptance. EXITKILL is the
                    # final kernel safeguard when this tracer thread exits.
                    try: signal.pidfd_send_signal(self.pidfd, signal.SIGKILL)
                    except ProcessLookupError: pass
        finally:
            os.close(self.pidfd)
            self.pidfd = None
            self.attached = self.paused = False
