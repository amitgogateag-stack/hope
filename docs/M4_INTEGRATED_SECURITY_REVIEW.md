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
