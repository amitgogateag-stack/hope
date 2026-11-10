# M4 same-interpreter exec regression — negative security evidence

Scope: isolated PR #88 session-proxy prototype. This does **not** authorize
PAPER admission, close A1, or modify HOPE's production verifier.

## Finding and reproducer

`security/session_proxy/test_exec_boundary.py` runs two different Python
programs in one PID. The first program waits for a signal, then calls
`os.execv(sys.executable, ...)` to replace itself with the second program.
The test reads the proxy's actual `observation(pid)` before and after.

On a Linux host where the second program runs under the same interpreter and
unchanged namespaces, `pid`, `start_ticks`, `executable_sha256` and
namespace links remain equal. The test **passes when it demonstrates this
identity collision**. It is a negative proof, not an admission security pass.

Run from the repository root on Linux:

```sh
python -m unittest discover -s security/session_proxy -p 'test_exec_boundary.py' -v
```

Do not treat this passing test as an authorization to use the current
`observation(pid)` for production trust. The test does not require root,
PostgreSQL, CAP_SYS_PTRACE or an external signer.

A second regression mutates a disposable Python module before the already-running\ninterpreter imports it. Its process/executable observation remains unchanged,\nshowing that interpreter identity also does not attest loaded Python code.\nBoth are negative evidence; neither is a production authorization test.\n\n## Required fix and acceptance

A reviewed native supervisor must establish an authenticated, non-worker-
forgeable process lifetime/exec generation, or prevent all subsequent exec
after a trusted launch. The supervisor's authenticated evidence must be bound
to the exact broker session and its signer; a worker-provided claim, Python
monkey patch, `/proc/PID/exe` hash or `pidfd` lifetime alone is insufficient.

Before production issuance can be enabled, add a separate *positive*
adversarial test in which the same-executable exec attempt is reliably denied
or causes session revocation **before financial COMMIT**. Also test execveat,
fork/inherited descriptors, source/module mutation and namespace changes.
Execute the proof on genuine Linux, with an independently controlled CI
harness and real PostgreSQL session binding.

This change intentionally does not modify the proxy or loosen any gate.
A1 remains OPEN. Engineering integration NO-GO; operational PAPER NO-GO.
PRs #85–88 remain unmerged. No AWS, migrations, or credentials changed.
