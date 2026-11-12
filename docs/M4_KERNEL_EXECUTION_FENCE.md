# M4 kernel execution fence: isolated engineering proof

Base: PR93 `1b3bb52bc8df27714c2be7ab75aee5969705b5de`, with the four
related strict negative-evidence accounting files from PR94
`27ec2702dca69244d01c61960151cb61910d8be4`. No PR merge is performed.
No human approval is recorded. R1 migration238, financial funding behavior,
strategies, risk and production DENY-only admission entry remain unchanged.

## Architecture and threat boundary

The existing observation-only prototype cannot distinguish a same-interpreter
exec. PR89's actual Linux collision reproducer remains unchanged as before
evidence; matching executable hash, PID/start ticks and namespaces is insufficient.

The selected incremental boundary remains the session-terminating proxy. It does
not forward worker-selected SQL. A distinct non-root observer UID with
CAP_SYS_PTRACE controls the peer using PTRACE_SEIZE, TRACEEXEC, TRACECLONE,
TRACEFORK, TRACEVFORK, INTERRUPT and EXITKILL. Kernel events invalidate the
lease; copied process IDs, environment variables, PostgreSQL GUCs and self-reported
evidence cannot establish it. The authenticated channel still checks actual
SO_PEERCRED and every packet's SCM_CREDENTIALS, and the proxy still owns the
actual PostgreSQL cluster/login/backend/start/xid connection being committed.

The prototype supports only a single-threaded target. It interrupts and checks
the task set before establishing the baseline. The real stopped /proc status must also report Threads=1, the exact Pid/Tgid
and this native tracer TID. A directory listing alone cannot establish absence
of concurrently exiting/untraced tasks. Thread/fork/exec events after this
validated baseline invalidate the lease. Existing multithreaded targets, unexpected
signal stops, unavailable ptrace and tracer-thread substitution fail closed.
The observer remains attached on the same native tracer thread. A final kernel
stop is held through actual SQL COMMIT, so the supported target cannot run a
different userspace program during that interval. Tracer death uses EXITKILL;
no detached or observation-only fallback is accepted. Broker concurrency is
bounded to eight handlers with existing packet/deadline limits.

The separate disposable signer checks the kernel's actual TracerPid and tracer
UID/Tgid, as well as its existing independent process and PostgreSQL observations.
It signs the proxy-created execution-lease transcript. Neither worker nor proxy
holds that ephemeral private signing key. This co-hosted test signer is NOT a
production isolated authority; CAP_SYS_PTRACE is still a broad privilege.

## Exact limitations

This proves exec/task exclusion only AFTER a kernel-stopped singleton baseline, not absence
of earlier exec or integrity of loaded Python code/memory. It does not attest
a production OCI image. Ptrace commands address numeric TIDs; pidfd liveness
and repeated observations are essential and require independent kernel review.
SIGKILL/root intervention and DB durability are not one atomic operation.
The stop-through-COMMIT fence is not a wall-clock acknowledgment guarantee.
The observer and broker remain trusted components, not adversary-controlled
issuers. No production credential, approval-writing role or new trust root is
provisioned. This is not installed into HOPE's financial path.

The production verifier remains DENY-only. All twenty mandatory production
acceptance requirements remain FAIL/unexecuted. A prototype case may be VERIFIED
only within its explicitly named scope after actual kernel/SQL assertions.
Unavailable native cases must fail the prototype workflow; no mock positive,
skip, xfail or borrowed acceptance label is used.

## COMMIT, crash and replay semantics

The proxy distinguishes denial before COMMIT, authenticated database constraint
rejection, a known committed result with lost delivery, and an uncertain COMMIT
response. It must never emit DENIED merely because transport/audit failed after
a successful COMMIT, nor automatically replay an uncertain operation. Recovery
experiments use the separate test authority's read-only database observation and
wait for the original backend to disappear. Restart creates a new kernel/session/
xid/challenge; an old signature cannot be reused. No production recovery adapter
or complete HOPE reconciliation proof is claimed.

HALT/revocation/generation keep the existing PostgreSQL control-row fence.
An owner mutation serialized before COMMIT rejects it; one ordered afterward
cannot cancel an already committed transaction. External policy transport and
independent CI administration remain outstanding external trust dependencies.

## Validation routes

- Unchanged PR89 Linux observer collision proof: before evidence, not acceptance.
- `tests/unit/test_execution_fence_contract.py`: unsupported/self-target refusals.
- `security/session_proxy/run.py`: genuine separate-UID ptrace, actual upstream
  PostgreSQL16/17, signed session challenges, replacement/thread/SCM attacks,
  COMMIT-stop observation, tracer death, acknowledgment loss and read-only recovery.
- PR94 strict twelve-denial accounting is retained. Its intentionally failing
  production gate is not made green by prototype passes.
- Existing full HOPE/R1/238 financial, expiry and migration matrix remains required.

No AWS/host check, operational PAPER activation, LIVE order, strategy change,
production grant or frozen-baseline modification is authorized or performed.
Engineering integration NO-GO; operational PAPER NO-GO pending the remaining
production attestor, image/code, signer isolation, adapter and independent review.

Kernel semantics reference: https://man7.org/linux/man-pages/man2/ptrace.2.html
(per-thread tracing, exec events, seize/interrupt and EXITKILL).

## Recorded engineering evidence

The initial native candidate167798aacc6df5bf8635c384608560a9108b4cf1 ran
24 actual prototype cases on each PostgreSQL16/17 job, with no failed/blocked
prototype cases. Kernel audit shows a real tracing stop through a blocked
COMMIT. Separate owner reads resolved zero effects after pre-COMMIT tracer death
and one effect after transport loss during COMMIT; old signed responses failed
on restart. This is implementing-agent evidence, not human approval or any of
the twenty mandatory production acceptance proofs.

Local pinned mypy1.15.0 caught an int/closed-pidfd lifecycle mismatch; the final
candidate adds an explicit closed-lease refusal and regression. Type checking
passes for the new fence and denial-accounting modules. Final-head CI and exact
counts are recorded in the delivery PR rather than inferred from the first run.

R2 remains separate in PR91: its authenticated bounded-reader primitive requires
an independently supplied digest. It is not integrated here; deriving a digest
from the inspected untrusted file would manufacture provenance. Descriptor,
no-follow, size and mutation guards remain untouched.

G7FX is retained in `docs/G7FX_RESEARCH_ROADMAP.md` as **NOT STARTED**. Its
independent research assessment does not activate a strategy or delay A1 testing.
