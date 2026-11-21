# HOPE December 1 PAPER acceptance gate (2026-11-19)

**Target:** controlled PAPER-only AWS acceptance by December 1, 2026. **Current verdict: NO-GO.**

This document is a release gate, not evidence that any criterion has passed.
Do not merge this draft security branch, activate a controller, enable LIVE,
change risk limits, or overwrite the frozen baseline to meet a calendar date.

## Required independently witnessed evidence

1. **Trusted signer custody:** signer private keys and trusted public-key registry
   owned by an independent, isolated authority; revocation survives restarts.
   Evidence: process isolation, key provenance, rotation/revocation tests,
   negative unauthorized signer tests. **OPEN**.
2. **Authenticated session binding:** independently observed PostgreSQL server,
   backend PID, session user, database, transaction ID, controller generation,
   and challenge freshness; reject replacement and replay. The current
   session-observation validator only checks supplied data. **OPEN**.
3. **Atomic transaction write-set witness:** witness all relevant writes and
   reconcile them with a transaction-scoped policy decision, including
   trigger/indirect writes, rollback, concurrency, and retries. **OPEN**.
4. **COMMIT-time fence:** authorization and revocation are checked in the
   actual PostgreSQL transaction with fail-closed abort on identity change,
   revocation, restart or stale policy generation. Current continuity
   prototype does not intercept COMMIT. **OPEN**.
5. **End-to-end PAPER lifecycle:** India/USA PAPER orders, restart recovery,
   broker reconciliation, idempotency, stale feed, failure injection and
   NO_TRADE behavior proven on disposable infrastructure. **OPEN**.
6. **Operational acceptance:** exact-head PostgreSQL 16/17 CI, independently
   reproduced security acceptance, signed release provenance, rollback
   rehearsal, read-only dashboard freshness and AWS health evidence. **OPEN**.

## Stop conditions

- Any of the 20 mandatory independent security acceptance requirements FAIL
  or unexecuted => **NO-GO**.
- A green prototype CI job is never a substitute for independent admission.
- No AWS deployment or live trading without separate explicit approval.

## Execution order

- Prioritize independent signer/session custody and COMMIT barrier over
  additional standalone denial-only helpers.
- Integrate write-set witness into real transaction tests.
- Run all negative and crash/restart cases on PostgreSQL 16/17.
- Collect immutable exact-head evidence; only then evaluate controlled PAPER
  activation and operational dashboard acceptance.

The December 1 date is a planning target, **not a safety override**.
