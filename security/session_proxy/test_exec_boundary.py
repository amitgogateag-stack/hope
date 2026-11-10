"""Reproduce the same-executable exec blind spot in the isolated M4 proxy.

This is a NEGATIVE security proof: matching /proc identity is not an
exec-generation attestation. No HOPE production authority is issued.
Run: python -m unittest discover -s security/session_proxy -p 'test_exec_boundary.py'
Linux only; intentionally no root, database, or broker credentials required.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import select
import subprocess
import sys
import unittest

from broker import observation


@unittest.skipUnless(sys.platform == "linux", "Linux /proc semantics required")
class SameExecutableExecBlindSpot(unittest.TestCase):
    def test_exec_of_same_interpreter_preserves_observed_identity(self):
        # The second stage uses a DIFFERENT Python program in the SAME process.
        # /proc/PID/exe, PID start ticks and namespace links can remain identical.
        stage2 = (
            "import os,sys;"
            "print('AFTER:'+str(os.getpid())+':SECOND_PROGRAM',flush=True);"
            "sys.stdin.read()"
        )
        stage1 = (
            "import os,sys;"
            "print('BEFORE:'+str(os.getpid())+':FIRST_PROGRAM',flush=True);"
            "sys.stdin.readline();"
            "os.execv(sys.executable,[sys.executable,'-c',"
            + repr(stage2) + "])"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", stage1],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        try:
            self.assertTrue(select.select([proc.stdout], [], [], 5)[0], "first stage timeout")
            before_line = proc.stdout.readline().strip()
            self.assertEqual(before_line, f"BEFORE:{proc.pid}:FIRST_PROGRAM")
            before = observation(proc.pid)
            proc.stdin.write("exec\n")
            proc.stdin.flush()
            self.assertTrue(select.select([proc.stdout], [], [], 5)[0], "exec stage timeout")
            after_line = proc.stdout.readline().strip()
            self.assertEqual(after_line, f"AFTER:{proc.pid}:SECOND_PROGRAM")
            after = observation(proc.pid)
            self.assertEqual(before, after,
                "This regression documents the identity collision; if the observer "
                "becomes exec-aware, update the proof and security review.")
            self.assertEqual(before["pid"], proc.pid)
        finally:
            proc.kill()
            proc.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
