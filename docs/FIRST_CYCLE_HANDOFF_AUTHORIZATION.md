# Independently authorized PAPER handoff intents

This subsystem verifies a separately supplied, short-lived authorization for one
handoff **intent**. It does not launch a process, import a provider factory, run a
cycle, resume HALT, submit an order, or certify operational readiness.
`--stage-only` remains non-executing. Actual stage-to-first-cycle worker wiring
is still blocked until a separately reviewed execution contract exists.

## Independent authority and runtime

The external authority supplies canonical signed
`hope.paper-first-cycle-handoff.v1` documents under
`first-cycle-authorizations/<document-sha256>.json` with detached `.sig`, selected
by `<request-sha256>.ref`. The verifier never issues or signs an authorization.
`runtime-trust/first-cycle-ed25519.pub` must differ from all existing deployment,
provider, data, rollback, artifact, release, builder, image and policy roles.
Current signed image policy must not revoke it. Missing evidence fails closed.

The exact schema binds release, receipt, factory, immutable stage identity,
controller kernel incarnation and full argv, independent controller runtime
manifest, stage runtime manifest, image, database identity and logical cycle key.
PAPER must be true, LIVE false, max_cycles exactly one, and lifetime at most 60
seconds. The controller cannot be the staged process. Its interpreter and runtime
must independently pass existing integrity/provenance/build checks in isolated
Python with bytecode disabled. Its canonical bootstrap entry must be a tracked,
unchanged Python file directly under the approved release's deploy/operations.
This module provides no executable controller entry point.

Verification holds the existing shared activation fence, verifies staged receipt
and mandatory runtime evidence twice, closes process/source/runtime/policy/ref
identity races, and rechecks expiry. `VerifiedHandoff.assert_current()` repeats
verification against the original content digest. Bounded read-only controller
inspection reports AUTHORITY_VERIFIED/HANDOFF_INTENT_ONLY, UNVERIFIED or BLOCKED;
execution_authorized is always false. It does not promote controller state.

## Durable audit and recovery

Migration 234 adds append-only public.paper_handoff_intents and
public.paper_handoff_outcomes. Authorization, request and logical cycle key are
unique. One committed intent cannot be reclaimed or silently resumed after a
crash. A missing outcome means RECOVERY_REQUIRED. ACKNOWLEDGED and ABORTED are
historical audit outcomes, never proof of a successful cycle or permission to
execute. A transaction rolled back before commit has no durable claim. The caller
owns commit; expired records committed later remain historical records only.

The repository revalidates authority before and after each insert and checks the
DB clock. PostgreSQL triggers enforce real persisted RUNNING control history,
its existing sequence/snapshot checks and global HALT serialization, time bounds,
parent/evidence/principal bindings, and a durable policy-generation floor per
policy authority. Fixed schema/search paths prevent temporary table substitution.
UPDATE, DELETE and TRUNCATE are refused. These protections do not defend against
a privileged database owner restoring an older database or disabling triggers.

Database identity hashes cluster system identifier, database name/OID and major
version, rejecting recovery servers and versions outside PostgreSQL 16/17.
Access to pg_control_system must already be approved for the connection; this
code grants nothing. See the [PostgreSQL control data documentation](https://www.postgresql.org/docs/17/functions-info.html#FUNCTIONS-PG-CONTROL).
The connection must set positive statement_timeout <= 30s, lock_timeout <= 10s
and idle_in_transaction_session_timeout <= 5s. The latter bounds an abandoned
transaction retaining the HALT advisory lock during filesystem revalidation.

Applying migration 234 to production, provisioning authority keys, accessing a
host and activating any process each still require separate authorization.
Financial schema/certification/risk/HALT/idempotency/reconciliation gates remain
mandatory for any future execution integration. There is no readiness bypass.

## Validation boundaries

Tests use ephemeral signing keys outside a disposable operations root and actual
Linux kernel identities. Stage and runtime witnesses in signed handoff tests are
explicit synthetic probes; separate adversarial tests exercise controller
runtime binding. PostgreSQL tests use disposable CI databases only, including
concurrent claims, interrupted commit/reconnect, revocation, expiry, policy
rollback, HALT, search-path substitution and direct-SQL mutations. They establish
repository safety, not external provider acceptance or AWS operating readiness.
