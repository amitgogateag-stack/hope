"""Native adversarial checks for bounded exporter-preflight diagnostics."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

SOURCE = Path(__file__).parents[1] / "deploy/operations/inspect_exporter_install.py"
spec = importlib.util.spec_from_file_location("exporter_bounded_command", SOURCE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


pytestmark = pytest.mark.skipif(os.name != "posix", reason="native POSIX process-group contract")


def test_bounded_capture_returns_exact_bytes_and_exit_status():
    command = [sys.executable, "-c", "import os; os.write(1,b'bounded')"]
    result = mod._bounded_capture(command, timeout=3, max_output=64)
    assert result.stdout == b"bounded"
    assert result.returncode == 0
    assert result.stderr == b""


def test_bounded_capture_rejects_output_before_child_deadline():
    command = [sys.executable, "-c",
               "import os,time; os.write(1,b'x'*4096); time.sleep(30)"]
    started = time.monotonic()
    with pytest.raises(ValueError, match="^UNIT_METADATA_OUTPUT_LIMIT$"):
        mod._bounded_capture(command, timeout=5, max_output=128)
    assert time.monotonic() - started < 3


def test_bounded_capture_kills_process_group_on_timeout(tmp_path):
    pidfile = tmp_path / "child.pid"
    source = """
import os,subprocess,sys,time
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
open(sys.argv[1],'w').write(str(child.pid))
time.sleep(30)
"""
    with pytest.raises(ValueError, match="^UNIT_METADATA_TIMEOUT$"):
        mod._bounded_capture([sys.executable, "-c", source, str(pidfile)],
                             timeout=0.5, max_output=128)
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail("diagnostic descendant survived timeout")


def test_bounded_capture_discards_unbounded_stderr():
    command = [sys.executable, "-c",
               "import os; os.write(2,b'secret'*200000); os.write(1,b'ok')"]
    result = mod._bounded_capture(command, timeout=3, max_output=16)
    assert result.stdout == b"ok"
    assert result.stderr == b""


@pytest.mark.parametrize("timeout,limit", [(0, 1), (21, 1), (1, 0), (1, mod.MAX_BYTES + 1)])
def test_bounded_capture_rejects_invalid_bounds(timeout, limit):
    with pytest.raises(ValueError, match="^UNIT_METADATA_UNVERIFIED$"):
        mod._bounded_capture(["unused"], timeout=timeout, max_output=limit)
