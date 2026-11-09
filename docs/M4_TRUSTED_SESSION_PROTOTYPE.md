# M4 trusted session boundary and expiry repair

HIGH A1 remains OPEN. Engineering integration NO-GO; operational PAPER NO-GO.
Production admission executable remains DENY-only. No HOPE approval producer, production
credentials/grants, AWS changes, strategy changes, activation or LIVE capability is added.
This work follows draft/unmerged PRs #85, #86 and #87 (bda32668051bb5ea8c86d0f1acb854af7a174050).
Historical migrations 001-237 and frozen baseline 7e9d341742818a97accce3e2301b1c7f96596795 remain unchanged.

## Expiry boundary repair

The former parametrized [expired] case used a three-second execution authority and
called execution_callbacks(), whose closing permit.assert_current() genuinely refreshes
authority. Under scheduling delay it expired there, before intended financial SQL.
It also attempted an INSERT after expiry, not a successful transaction rejected at COMMIT.
No authority refresh is bypassed and no production code or deadline is relaxed.

The three-second deadline is retained in the before-admission expiry test. It waits four
real seconds, then requires DEPLOYMENT_EVIDENCE_INVALID and zero execution/economic rows.
A second admission test uses the normal signed fixture lifetime and stalls after the actual
execution INSERT until PostgreSQL's clock reaches its original expiry. It asserts the INSERT
was reached, then the original closing refresh rejects and rolls back. This deterministically
reproduces the earlier failure boundary using real PG/clock, rather than requiring a lucky
scheduler stall in a three-second setup. The normal lifetime is not extended or modified.

The financial-COMMIT test first completes ordinary, genuinely signature-checked admission
and every production closing refresh. A SQLAlchemy COMMIT event checks FIVE economic effects,
a same-xid financial witness and a still-live authority inside that transaction. Only then
it waits with bounded active PostgreSQL queries until the ORIGINAL signed authority expires,
and releases the real COMMIT. It requires PAPER_STARTUP_FINANCIAL_COMMIT_UNVERIFIED, exactly
one reached financial COMMIT event and zero committed effects/orders/fills/transaction
ledger/witness/completion/outcome rows. It cannot pass on an earlier admission exception.
It deliberately does not squeeze expensive signature setup into the separate three-second
admission experiment or replace the DB clock. The old 3-second limit is neither extended
nor removed; this additional COMMIT proof uses the existing normal signed lifetime.

## Concrete architecture choice

| Option | Deployment and binding | Decision |
|---|---|---|
| A: local session-terminating proxy | Local kernel credentials authenticate controller; proxy alone holds actual upstream connection and owns BEGIN/COMMIT; bounded command protocol excludes worker SQL | Smallest viable prototype; selected |
| B: server attestation extension | Reviewed server extension must bind authenticated local channel to backend/transaction, expose non-forgeable state and manage kernel observations remotely | Deferred: privileged extension installation/review, managed-PostgreSQL portability and host-to-server attestor still required |

Do not implement A as a transparent arbitrary-SQL tunnel: an attacker could alter constraints,
transaction mode, principal or protocol. This prototype terminates a narrow JSON-over-Unix
SOCK_SEQPACKET protocol and executes fixed SQL. Integrating existing SQLAlchemy HOPE financial
operations with such a constrained boundary requires a separately reviewed adapter. It is
not supplied by granting arbitrary SQL to workers. B does not remove the host trust problem.
No PostgreSQL superuser application role is used by the prototype owner, broker or worker.

## Implemented isolated Linux prototype

Source: security/session_proxy/{broker.py,worker.py,run.py,schema.sql,requirements.txt}.
Workflow: .github/workflows/m4-trusted-session-prototype.yml. No HOPE migrations are installed
by the prototype; its economic ledger is in session_prototype and is NOT a PAPER simulation.
The ordinary CI/expiry tests separately exercise genuine HOPE financial controls with the
existing explicitly modeled external-approval fixture.

A privileged TEST orchestrator provisions fresh random PostgreSQL owner/broker/worker LOGINs,
then starts the broker as UID 21001 and controllers as UIDs 21002/21003. The broker has only
CAP_SYS_PTRACE to observe a different UID's /proc executable and namespaces. It is not root,
a PostgreSQL superuser, schema owner, policy writer or authority signer. A root launcher
is needed only in disposable CI for UID/capability setup. Deployment requires review of
this observer capability or a smaller privileged observer service; absence is BLOCKED.

The worker never gets the broker DSN. Configuration mode 0600, separate UIDs, no role
memberships and root-owned source directories are required trust boundaries. The issuer
private key exists only in the test orchestrator; it is never sent to worker or broker.
All credentials are random and disposable. The adversary cannot read broker configuration
or assume its OS/DB identity without breaking the trusted kernel/host boundary.

## Challenge and actual session identity

1. SO_PEERCRED obtains the connection's real PID/UID/GID. pidfd_open pins process lifetime.
   Broker observes /proc PID start ticks, executable SHA-256 and mnt/pid/net/user namespace
   inode links; it compares executable/namespaces to independently provisioned expectations.
2. SO_PASSCRED/SCM_CREDENTIALS validates the actual sender PID/UID/GID on EVERY packet.
   A forked process inheriting the socket cannot borrow the original connection identity.
3. Broker opens its own non-superuser PostgreSQL connection and READ COMMITTED transaction.
   It queries pg_backend_pid(), pg_current_xact_id(), session_user, database OID, backend_start
   and cluster system_identifier ON THAT SAME CONNECTION. No worker backend PID, GUC,
   application_name, namespace digest or Permit object is accepted.
4. Broker creates a 256-bit random one-use challenge. The domain-separated canonical
   transcript binds observed kernel identity, actual DB session/xid, issuer role, scope,
   random cycle, source-tree digest, policy generation and a THREE-SECOND DB-clock lease.
   Worker relays an Ed25519 signature from the separate disposable authority. That signer
   independently checks /proc and pg_stat_activity backend start/xid/login/database/cluster.
5. Broker verifies the pinned Ed25519 public key and exact transcript, re-observes the peer,
   writes its approval and prototype effect, then awaits one commit packet. It rechecks
   per-message credentials, pidfd and process observations immediately before COMMIT.
   Duplicate nonce and effect primary keys plus fresh challenge/session/xid on reconnect
   prevent replay. Borrowing another session's signed response fails verification.

Trust limitations: /proc executable hash proves interpreter bytes, NOT loaded Python source,
process memory or an OCI image. A same-binary exec has no reliable exec-generation counter
in this protocol; pidfd/starttime alone do not prove absence of exec. These are BLOCKED,
not silently synthesized or declared verified. The production release image/provenance
chain is not integrated. The prototype's source digest is a trusted configured input,
not an observed production image. CAP_SYS_PTRACE is broad and could compromise a co-hosted
signer: production requires an isolated remote/hardware signing authority or an appropriately
scoped observer. A malicious trusted broker remains inside the TCB.

## Database privilege/COMMIT coordination

| Principal | Privileges |
|---|---|
| disposable admin | CI provisioning only; not application execution |
| owner LOGIN | prototype schema/functions ownership; CREATE database privilege for schema DDL |
| broker LOGIN | schema usage, SELECT, INSERT approvals/effects, execute fixed check; read own cluster identity |
| worker LOGIN | no prototype schema/table privileges; cannot SET ROLE to broker |

The worker cannot submit SQL. Broker cannot UPDATE policy, reclassify principals, disable
triggers, alter schema or self-grant rights. SECURITY DEFINER check_commit has a fixed
pg_catalog/session_prototype search path, is owner-owned and is not public-executable.
At actual COMMIT the deferred database trigger takes a SHARE row lock on the current
control row and checks current HALT/revocation/generation, DB clock expiry, actual backend,
xid and session_user. It rejects early SET CONSTRAINTS and noncanonical commit contexts.
Control mutations are owner UPDATEs of that same row. If HALT/revocation commits while the
financial transaction is running but before its COMMIT fence, that COMMIT is denied. If
COMMIT gets the SHARE lock first, a policy UPDATE waits and takes effect afterward. This
is serialization, not an absolute promise to cancel an already serialized transaction.

Kernel observations/signature and PostgreSQL WAL durability are not atomic. A process may
change after the last observation. Expiry at a trigger does not promise client acknowledgment
before a wall-clock instant. External revocation must be ingested through the coordinated
control authority; an unavailable/delayed external revocation channel cannot give absolute
revocation. This prototype has no production unknown-commit reconciliation adapter and no
production audit/recovery guarantees. No operational readiness follows from prototype passes.

## Executable attacks and evidence classification

The standalone runner attempts a positive prototype transaction, borrowed response,
reconnection, executable replacement, wrong UID, genuine mount-namespace mismatch,
SCM credential mismatch through inherited socket, replay, authority revocation during
execution, HALT during execution, COMMIT after a three-second lease and direct worker SQL.
Each is VERIFIED only after its specific assertion and zero unauthorized committed effects.
Host capability/namespace failures are BLOCKED; assertion failures are FAILED. Required
scenario errors cause nonzero exit. Positive economic effects are only isolated prototype
ledger records and never production PAPER evidence. JSON includes kernel/session observations;
Markdown and JUnit include per-case outcomes. Artifact digests and exact source SHA are saved.

PR-controlled workflow and harness code are NOT independent approval. Moving a reviewed,
pinned harness to a separately permissioned repository or required base-branch workflow
and applying branch protection/CODEOWNERS is BLOCKED pending repository administrator
review. This task does not install repository security policy or manufacture that trust.
Source is kept standalone so a trusted runner can check out the candidate separately.

## Readiness and recommendation

HIGH A1 OPEN until image/code/exec identity, signer isolation, HOPE adapter, current policy
coordination and adversarial lifecycle/recovery evidence are reviewed end to end. MEDIUM:
PR-controlled CI trust, broad observer capability and external observation/COMMIT gap.
Use option A as the next isolated engineering experiment; do not enable production issuance.
See the linked delivery PR and Actions artifacts for exact commits/counts and failed/blocked
results. A prototype pass cannot replace those missing guarantees or independent review.

Primary references:
- https://man7.org/linux/man-pages/man7/unix.7.html (SO_PEERCRED/SO_PASSCRED)
- https://man7.org/linux/man-pages/man2/ptrace.2.html (/proc access checks)
- https://www.postgresql.org/docs/16/sql-createtrigger.html (deferred constraint triggers)
- https://www.postgresql.org/docs/17/sql-set-constraints.html (early validation)
