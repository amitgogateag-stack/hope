# Restricted LAB snapshot refresh qualification

This candidate observes an independently selected disposable PostgreSQL database.
It does not authorize PAPER execution, financial writes, production readiness or
independent admission. Existing financial COMMIT, write-set, provenance, strategy
and risk gates are unchanged. PR #117's deployed static HTML is not an exporter
or an approved Python/runtime deployment.

## Root causes and code boundary

The previous refresher required only a database name and JSON contract. Its caller
could supply an owner-capable connection and its database digest was name-derived.
The mandatory `ObserverProfile` binds an exact LOGIN to the existing cluster,
database OID/name and PostgreSQL-major identity digest. It verifies READ ONLY,
session_user=current_user, no reachable privileged roles, DDL/TEMP, table/column/
sequence mutation rights or user-schema executable SECURITY DEFINER functions.
Public capability closure is reused from the financial lifecycle observer. The
same session is checked again after collection. There is no database-name or owner
fallback when cluster metadata permission is absent. No production grants or
classification/approval rows are created. Missing permissions stay blocked.

Only the refresher requires this profile; historical generic exports remain
compatible. A bounded read-only repeatable-read transaction collects real rows
and uses the database transaction timestamp. Optional absent schema/permission
projections remain UNAVAILABLE, never inferred zero balances or trading results.
The existing persistent flock, private no-follow files, fsync/atomic replacement,
receipt binding and stale-data contract remain in force. Snapshot and receipt
are two separately atomic files: a crash between them is a mismatched pair and
must remain unavailable, never CURRENT. A failed read preserves last-good bytes;
its FAILED receipt cannot make those old bytes fresh.

`dashboard_release.py --observer-refresh` is a fixed one-shot dispatch through the
existing OS bootstrap. Before any refresh/database import, it authenticates the
release and independently approved runtime manifest, requires verified release-build
provenance before interpreter execution and again before database dispatch, reauthenticates after the
interpreter handoff, requires Python 3.13/exact venv and runs the existing PAPER
isolation check. Deployed commit must equal the authenticated release directory.
Only HOPE_DATABASE_URL crosses the observer handoff; GitHub and injected Python/
loader environment variables do not. The qualified route rejects interval loops,
so each invocation rechecks trust and isolation. Direct module invocation is a
LAB collection API, not evidence of release/runtime/PAPER qualification.

The checks do not cryptographically authenticate a remote publisher, stop a
same-UID writer, or coordinate financial revocation with COMMIT. They cannot close
HIGH A1 or substitute for an independent signer/session/host acceptance process.
The observer profile must come from separately reviewed operator configuration.

## Candidate persistent service (not installed or accepted)

`deploy/operations/hope-dashboard-observer.service.in` and its timer propose a
separate private state directory and Unix-socket-only observer. Replace all four
placeholder values only from reviewed release and identity evidence. The template
has no installer and cannot be enabled as-is. The timer schedules one attempt on
each minute boundary, with one-second scheduler accuracy, no randomized delay
and persistent catch-up after reboot. systemd does not overlap an active oneshot;
the persistent flock also excludes a second publisher. A 50-second timeout bounds
a hung attempt; neither timing setting guarantees a successful fresh snapshot.

This is a new service identity/configuration, not a replacement accepted by the
old activation journal or pinned legacy unit hashes. Never rewrite those hashes,
change the frozen release, move global current, invent approved runtime manifests,
change database grants or reclassify an absent PAPER service to force preflight.
Existing PAPER-absence review remains mandatory. The reviewed runtime and source
must be independently approved and accessible to the restricted OS principal.

Before requesting host changes, produce the exact source/archive hashes, approved
runtime-manifest evidence, exact template expansion and current unit/journal/
source/database identity inventory. Verify that the proposed observer can read
pg_control_system metadata without broader rights. Use a separate review for any
required minimal metadata EXECUTE grant; this code does not issue one.

A paired read-only HTTP reader must have reviewed access to the private files and
serve /snapshot.json plus /refresh-status.json using the existing dashboard_server
contract. The static preview cannot expose these receipts. Do not make its webroot
writable, weaken private-file ownership/mode checks or copy snapshots into a public
root. A publisher-sharing UID is not independent provenance. No web service,
network exposure or access-control change is authorized by these templates.

## Required disposable and host acceptance

Native PG16 and PG17 tests exercise actual restricted LOGINs, wrong owner/login/
cluster binding, missing metadata permission, column/sequence/non-inherited role/
TEMP/SECURITY DEFINER/custom-schema capability drift, last-good preservation and
successive database timestamps. Administrator grants in these fixtures are
explicitly synthetic test setup, not operating evidence. Bootstrap unit tests
model external runtime and PAPER checks; they do not approve a host. Existing
native publication crash/concurrency tests must pass unchanged.

Host qualification is pending a concrete, separately approved installation:

1. Keep the current static preview and historical snapshot unchanged. Back up the
   exact affected units/configuration/private snapshot and receipt with hashes.
2. Stage the approved release/runtime without changing global current or PAPER.
   Install only the reviewed observer/reader units and private paths, then run a
   single qualified read-only observation. No financial DB writes or migrations.
3. Observe at least three minute-boundary attempts against the genuine database.
   In Chrome verify increasing database generated_at, exact commit, bound receipts
   and accurate freshness. Equal data counts are valid when the database is idle.
4. Rehearse restart/reboot catch-up and publisher death during publication in a
   disposable host first. A torn/missing receipt must fail closed; next complete
   publication must recover. Verify bounded SQL/lock waits and no duplicate writer.
5. Rehearse rollback by stopping only the new timer/service, restoring the exact
   pre-change reviewed unit/configuration/file set and checking its hashes and
   observable behavior. Do not roll back or mutate any financial database state.

No host persistence, restart, rollback, genuine-feed availability or browser
successive-refresh claim is made by repository tests or a green CI result. The
known source/runtime/legacy-service-template mismatch, separately reviewed PAPER
absence, deployment approval and actual private reader configuration are required
before automatic AWS refresh can be called restored.

## Functional continuation and release blockers

Database inventory so far has not established usable provider bars or qualified
strategy inputs. Retain independent historical observations; do not manufacture
feeds, signals, orders, fills or portfolio balances. Disposable India/USA lifecycle
fixtures remain simulations with explicitly modeled external approvals. Real
market ingestion requires genuine provider/dataset/session/calendar contracts,
PIT/freshness and strategy qualification. HIGH independent production admission,
trusted publisher/session infrastructure and human security review remain required;
PAPER activation and LIVE remain disabled. G7FX stays deferred.
