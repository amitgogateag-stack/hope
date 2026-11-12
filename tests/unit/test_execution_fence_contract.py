"""Refusal contracts only; genuine positive kernel proofs run on Linux CI."""
import os
from pathlib import Path
import runpy

import pytest

module=runpy.run_path(str(Path(__file__).parents[2]/'security/session_proxy/execution_fence.py'))


@pytest.mark.parametrize('pid',[0,-1,True,'1',None,object(),os.getpid()])
def test_invalid_or_self_target_is_never_attached(pid):
    with pytest.raises(module['FenceDenied'],match='EXEC_FENCE_UNSUPPORTED_TARGET'):
        module['ExecutionFence'](pid)


def test_closed_execution_lease_cannot_be_checked_or_reentered():
    fence=module['ExecutionFence'].__new__(module['ExecutionFence'])
    fence.pidfd=None
    fence.attached=False
    with pytest.raises(module['FenceDenied'],match='EXEC_FENCE_CLOSED'):
        fence.check()
    with pytest.raises(module['FenceDenied'],match='EXEC_FENCE_CLOSED'):
        with fence.stopped():
            raise AssertionError('closed lease entered a commit barrier')


@pytest.mark.parametrize('change',['threads','tracer','pid','tgid','not_stopped','missing'])
def test_nonmatching_kernel_stop_metadata_is_refused(change):
    # Negative parser cases only. Actual positive /proc data is tested by the
    # separate-UID native runner, never supplied by a trading worker.
    fields={'Pid':'123','Tgid':'123','TracerPid':'456','Threads':'1','State':'t (tracing stop)'}
    if change=='threads':fields['Threads']='2'
    elif change=='tracer':fields['TracerPid']='789'
    elif change=='pid':fields['Pid']='789'
    elif change=='tgid':fields['Tgid']='789'
    elif change=='not_stopped':fields['State']='S (sleeping)'
    else:fields.pop('Threads')
    with pytest.raises(module['FenceDenied']):
        module['require_singleton_stop']('\n'.join(k+': '+v for k,v in fields.items()),123,456)
