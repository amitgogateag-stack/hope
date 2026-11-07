"""Interrupt the exact stop primitive selected by the production CLI."""
import signal
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize('name',['SIGTERM','SIGINT'])
def test_shutdown_signal_cannot_reenter_a_nonreentrant_condition_lock(name):
    code='''
from contextlib import nullcontext
import signal,sys,time
from hope.api.paper_service import Event
stop=Event()
signal.signal(getattr(signal,sys.argv[1]),lambda *_:stop.set())
# This is the interruption window in threading.Event.wait(): a signal may
# arrive while the main thread owns its condition lock. Never reacquire it
# from a signal handler. A lock-free stop request has no condition to acquire.
with getattr(stop,'_cond',nullcontext()):
 signal.raise_signal(getattr(signal,sys.argv[1]))
assert stop.is_set()
assert stop.wait(30) is True
print('STOP_REQUESTED',flush=True)
'''
    result=subprocess.run([sys.executable,'-c',code,name],capture_output=True,text=True,timeout=3)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='STOP_REQUESTED'


def test_idle_wait_observes_signal_request_without_waiting_for_poll_deadline(monkeypatch):
    from hope.infrastructure import signal_stop as module
    stop=module.SignalStop();clock=[0.0];sleeps=[]
    monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    def sleep(duration):
        sleeps.append(duration);clock[0]+=duration
        if len(sleeps)==2:stop.set()
    monkeypatch.setattr(module.time,'sleep',sleep)
    assert stop.wait(30) is True and sleeps==[.1,.1]
    assert stop.wait(0) is True
    assert module.SignalStop().wait(0) is False


@pytest.mark.parametrize('value',[True,-1,301,float('inf'),float('nan'),None,'30'])
def test_invalid_idle_wait_is_rejected(value):
    from hope.infrastructure.signal_stop import SignalStop
    with pytest.raises(ValueError,match='PAPER_STOP_TIMEOUT_INVALID'):SignalStop().wait(value)
