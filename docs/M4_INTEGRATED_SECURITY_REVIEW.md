# Integrated startup and provider security review

Candidate parents are preserved PR83 cb82eb12e95a9c1b17c08c917dab3919e0b79783
and PR84 3fec053a3a9358894d1699f72baaa7d023c6c76a. Integration targets
p0-pit-context; neither source PR nor the frozen baseline is changed/merged.
The exact proposed SHA and final source/PR PG16/17 evidence belong in the
integration PR. This document does not manufacture an approval for that SHA.

## Review independence and limits

Bandit1.9.4 runs independently of the implementation's assertions in an isolated,
pinned tool environment. Full-source static scan: zero HIGH, nine MEDIUM and
20LOW findings at the initial integrated scan. CI retains a fatal HIGH-severity
scan of all production source, plus all ordinary regression/security/concurrency
checks and the unchanged coverage threshold. No checks or findings are suppressed.
The combined changed-source scan reported six MEDIUM SQL-string warnings and two
LOW exception/fallback warnings. Bound-value SQL and identifier/column/table
validation make these reviewed constructions non-injectable; generic scanners
cannot infer those contracts. Existing fail-closed exception fallbacks do not
report success or disclose provider/database errors.

The implementing agent also performed adversarial source review and reproducible
fault experiments. That is **not independent human approval**. A separate human
security reviewer must review the complete combined diff, namespace/observer trust,
least-privilege grants, signature-role operation and real host acceptance before
merge/deployment. Static analysis cannot certify trading safety or AWS readiness.

## Findings and disposition

| Finding | Severity | Evidence and fix |
|---|---|---|
| F1: tuple snapshot retained mutable RawMarketBar objects | HIGH | A final provider.source callback changed validated event/availability/ingestion timestamps and the pipeline persisted a different window. Reproduced with a failing regression. Private concrete raw-bar snapshots now retain immutable scalar/UTC values; non-primitive numeric hooks/subclasses are refused before coercion. |
| F2: identity-map snapshot retained caller-owned UUID objects | HIGH | object.__setattr__ changed the instrument after the mapping snapshot. Reproduced with a failing regression. Snapshot now constructs independent UUID values before transport. |
| F3: destination dataset UUID remained aliased across transport | HIGH | Provider callback redirected the sink destination despite an earlier UUID type check. Reproduced with a failing regression. The authoritative pipeline captures a private destination UUID before any provider action. |
| F4: startup controller could borrow stage image identity | HIGH design risk | New startup admission now requires an independently signed namespace digest, real matching mount/PID/user/network/root identity and non-root UID, plus equality of independently attested release/image runtime manifests. A host lookalike is rejected even with matching source SHA. Missing genuine observer/image evidence blocks operation. |
| F5: dispatch-time expiry alone leaves a commit-time gap | HIGH design risk | New financial-transaction callback revalidates before claim and before commit. Immutable commit witness and deferred job constraint require fresh evidence at actual COMMIT; missing/expired witnesses roll back all economic rows. Real pg_sleep expiry and lost-ack tests exercise the boundary. |
| F7: two prebootstrapped authorities racing execution | HIGH design risk | The execution INSERT now repeats the unresolved-database-execution exclusion under the established control lock. A concurrent two-receipt regression requires exactly one winner; startup-time inspection alone is insufficient. |
| F8: zero-exit supervisor is not a successful cycle | HIGH design risk | A stop arriving after consumption but before the loop can return zero with no job. Startup now authenticates durable certified completion after supervisor return before reporting CYCLE_COMPLETED; a real SIGTERM-after-claim regression requires RECOVERY_REQUIRED, zero jobs/effects and consumed authority. |
| F6: ambiguous financial search path | defense in depth | Controlled startup is explicitly public-only. Effective schema order is checked, API connections pin pg_catalog/public/pg_temp, and audit SQL is qualified. Private and temporary shadows cannot substitute financial evidence. Private read-only dashboard schemas remain compatible. |

F1-F3 failed on the combined parent before fixes; all three passed after fixes.
No unresolved HIGH/CRITICAL finding is knowingly accepted or hidden. Final CI and
separate human review are still required; this statement is not operational GO.

## Invariants reviewed

- Bootstrap authority max_cycles=0 cannot import a factory, dispatch a job or make
  ordinary CURRENT true. Distinct execution key/scope/digest is mandatory.
- Content-addressed, independently signed <=60second authorities bind original
  intent, exact kernel/argv/runtime/image/provider/receipt/database/cycle identity;
  startup additionally binds approved public catalog and actual namespace.
- Every current key/policy/ref/document/source/process binding is closed and
  revalidated. Durable policy floors reject rollback; roles cannot be reused.
- Controller journal and existing activation fences remain authoritative. HALTED,
  interrupted, corrupt or mismatched journals are never auto-adopted for startup.
- Actual least-privilege synthetic LOGIN is used in disposable PG tests. Production
  code grants no permissions or keys. No unrestricted engine socket is authorized.
- Intent/startup/execution claims commit before dispatch, are append-only and
  unique, and remain consumed after uncertain outcomes. No automatic retry/ABORT
  or HALT clearing exists. An unresolved execution blocks another startup.
- Existing certified scheduler, family/code/research eligibility, sizing/risk,
  PIT/freshness/calendar, job/effect idempotency and reconciliation remain in use.
  Ordinary execution's CURRENT requirement is unchanged.
- Completion requires real financial success plus certified outcome and
  reconciliation audit. ACK and diagnostics alone never authorize execution.
- No new strategy, directional eligibility, risk thresholds, LIVE path, AWS
  connection, host launch, installation, production write or merge is performed.

## Remaining operational dependencies

Real independent signing authorities; approved host/container namespaces and
read-only observer/launch design; real factory/provider/dataset/calendar evidence;
least-privilege credentials/grants; current backup/restored-host rehearsal; final
review/merge decision and separate production installation/activation approvals.
Missing evidence remains BLOCKED. Historical AWS release f17a54929a8ecb7745a62c118893b55494aeb2f1,
PG16.15/schema008 and stale dashboard observations do not establish current facts.


## Final financial security audit (supersedes previous engineering GO)

**Engineering integration NO-GO; operational PAPER NO-GO.** The source/PR CI
links in the PR identify the exact tested commit; prior green results do not
approve this changed candidate. Human review remains mandatory.

Confirmed HIGH F9: four restricted-login direct-SQL regressions committed new
PAPER cash in bootstrap-only mode, without a completed job, after completion,
and after execution expiry. Regression-first source CI1686/run42602410027 and
PR CI1687/run42602420271 failed all four cases on PostgreSQL16/17. A deferred
job_runs trigger cannot cover transactions that never modify job_runs.

Append-only migration236 preserves all historical001-235 source/checksums.
It guards DML on thirteen financial relations, persists an immutable one-cycle
financial transaction identity, binds the witness to that actual xid8, requires
SUCCEEDED plus certified outcome and current expiry at COMMIT, and prevents
reuse in another transaction. Per-row deferred checks follow existing financial
projection checks. SET CONSTRAINTS cannot spend the expiry check early: controlled
validation accepts the driver's canonical COMMIT only, refusing immediate,
prepared and procedure contexts. Startup logins cannot possess TEMPORARY, schema
CREATE, privileged or reachable privileged roles, preventing worker-installed
late deferred trigger code. Bootstrap verifies this before consuming authority.
Truncation by scoped startup logins is forbidden. Existing ordinary principals,
financial/risk/eligibility/PIT gates and private dashboard copies are unchanged.
A consumed startup principal must never be repurposed as an ordinary worker.
This is commit-phase validation, not a guarantee that WAL fsync or the client's
acknowledgment finishes before an absolute wall-clock deadline.

**Unresolved HIGH A1: database admission is not an independent signature
verifier.** The existing worker LOGIN can INSERT authority audit rows. PostgreSQL
checks metadata and fences but does not authenticate Ed25519 documents, scope,
current filesystem refs or external revocation policy. The initial native security suite (13 cases per PG16/17 job) confirmed that
application verification rejects a wrong scope while direct SQL can still
persist that document's digest as an execution row. No economic action is taken by the scope characterization. A second
characterization records synthetic cash before admission: until the login has
consumed a startup claim, these guards cannot distinguish it from an ordinary
financial writer. This requires trusted pre-admission role classification, not
an allowlist inferred from worker-controlled rows. This test records a vulnerability, not approval or a
weakened security expectation. No test is xfailed or assertion relaxed.

Meeting the requested invariant against a SQL-capable/compromised worker requires
a separately trusted admission writer/verifier and removal of worker authority
INSERT privileges, or a reviewed database cryptographic/commit enforcement design.
The current in-process repository cannot both retain worker INSERT capability
and claim independent database authorization. No issuer, trust key, privileged
extension, production grant or approval is invented here. Do not merge or activate
this candidate as production-ready merely because transaction regressions turn
green. Independent security review and that architectural correction remain
engineering blockers, in addition to all existing genuine host/provider/data/
calendar/restore/signing and activation prerequisites.


Additional scope hardening binds the consumed session login as well as current
role, so SET ROLE cannot erase an established startup restriction. Every one of
the thirteen tables is checked for enabled DML, truncate and deferred row guards.
Failed terminal jobs cannot commit partial financial work. The full source and
PR PG16/17 results and exact SHA in the PR supersede the initial installation
failure caused by psycopg interpreting a PL/pgSQL format placeholder; identifier
quoting fixed the installation without changing migration runner behavior or
assertions. These additional checks do not resolve HIGH A1.


Confirmed MEDIUM F10: an expired scheduler lease connection could mask the
primary financial-COMMIT rejection during cleanup. A prior PG17 full run failed
its strict expiry-error assertion; server logs show the scheduler idle-timeout
and financial rejection on different backend PIDs milliseconds apart. A local
regression reproduced replacement of the primary exception. Cleanup now discards
the failed session and preserves the primary error with fixed diagnostic notes;
a cleanup failure after otherwise successful work still fails closed. Real
backend-termination cases verify both paths and lock release. No timeout,
assertion, expiry policy, lock ownership or retry behavior is loosened.


## Admission-trust remediation follow-up: containment, not completed verification

The latest HIGH A1 report was checked against PR85 reviews (none adding a separate
finding), the actual repository path and real restricted-LOGIN regressions.
At remediation regression commit75b65e89550760714c8e4d8d998da354602a0d9e,
source1696 and PR1697 each failed both strict denial cases on PG16 and PG17:
2 failed,16 passed per job, DID NOT RAISE. A wrong-scope digest became execution
metadata; an unadmitted login committed synthetic cash. These were real
localhost disposable databases, not production incidents.

Append-only migration237 adds an owner-protected principal registry bound to
both role OID and login name. Unknown financial writers are denied before any
claim; a worker cannot choose ORDINARY classification or escape via SET ROLE.
Only explicitly classified controlled principals can insert startup records.
Seven handoff/startup tables require an exact JSONB row approval for that login,
table, authority and bounded expiry, written through a separate owner connection.
Workers cannot create those approvals even with accidental broad INSERT grants.
All registry/approval rows are immutable, with no inferred/backfilled trust.
The existing transaction, risk, certification, HALT, expiry, recovery and
reconciliation gates remain mandatory. Database owners remain trusted DDL/data
administrators; they are already prohibited as operational workers by preflight.
Historical standalone owner-written handoff audit remains non-executing.

**Important limit / unresolved HIGH A1:** this is a real fail-closed database
containment boundary, not a completed independent verifier service. No production
approval producer, login, signature, grant or admission is supplied. Production
startup is BLOCKED without exact independently supplied approvals. A secure
producer must independently observe the target controller/kernel incarnation,
attest its runtime/namespaces/approved observer, validate all independent signed
roles and current refs, bind the database login, and coordinate policy revocation
and closing COMMIT admission without giving the worker its database capability.
The existing self-process verifier cannot simply be run as a different process
and certify itself on behalf of a target; accepting an in-process Permit as a
remote broker request would reproduce the flaw. Designing that authenticated
peer/observer and current-policy protocol safely is the remaining blocker.
No production callable approval-issuing API is added to hide that missing boundary.
Owner-supplied approvals alone must never be represented as verified signatures.

Positive PostgreSQL lifecycle tests explicitly model that external authority in
tests/admission_fixture.py using the real separate disposable database-owner
connection after genuine ephemeral signature verification. Runtime/observer
witnesses remain explicitly modeled, as before. These test-only approvals are
not production evidence and do not establish that the missing service exists.
A separate regression removes that model and requires even a genuine in-process
Permit to fail with zero handoff, startup and economic rows. Both original HIGH
characterizations are strict denial/no-effect assertions now, not xfails.

Engineering integration NO-GO and operational PAPER NO-GO remain in effect.
Independent human review of both PR85 and this isolated follow-up is required.
No AWS, credentials, host access, production migration or operational execution
is authorized or performed. This follow-up does not alter PR85 or the frozen
baseline. The final exact-source/PR matrix evidence is recorded in its PR.


Full regression also exposed the historical M3 acceptance fixture's admin-session
SET ROLE model. It is replaced with a genuine restricted LOGIN and explicit
separate-owner ORDINARY classification in the disposable database. Current user
and session user must both equal that login. All original acceptance, concurrency,
rollback and no-replay assertions remain unchanged; no production substitution
exception is introduced. These legacy cases also run in the early PG preflight.
