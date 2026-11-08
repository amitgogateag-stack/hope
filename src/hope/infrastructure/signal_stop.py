"""Main-thread signal requests without reentering threading locks."""
import math
import time


class SignalStop:
    """A signal handler only stores a flag; the supervisor polls while idle.

    threading.Event.set acquires a non-reentrant condition lock. A Python
    signal handler can interrupt Event.wait while that same thread owns it,
    deadlocking graceful shutdown. No lock or I/O belongs in this handler.
    This is a main-thread supervisor control, not a general threading Event.
    """
    def __init__(self):self._requested=False

    def set(self):self._requested=True

    def is_set(self):return self._requested

    def wait(self,timeout):
        if (isinstance(timeout,bool) or not isinstance(timeout,(int,float))
                or not math.isfinite(timeout) or not 0 <= timeout <= 300):
            raise ValueError("PAPER_STOP_TIMEOUT_INVALID")
        deadline=time.monotonic()+timeout
        while not self._requested:
            remaining=deadline-time.monotonic()
            if remaining <= 0:return False
            time.sleep(min(remaining,.1))
        return True
