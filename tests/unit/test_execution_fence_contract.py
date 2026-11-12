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
