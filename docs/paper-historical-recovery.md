# Historical certified PAPER recovery

An M2 PostgreSQL reproduction committed a filled run with five effects and
correct accounting, then demoted its strategy before the service's audit step.
The ordinary service correctly refused current admission, but could no longer
finish the historical receipt: SUCCEEDED / five effects / zero reconciliation
audits. Re-promoting a strategy merely to finish that audit would be incorrect.

The bounded recovery API and command independently authenticate explicit
historical certified runs. They never call a factory, generate signals, claim a
job, simulate an execution, apply accounting or modify eligibility. Current
demotion, replacement promotion epochs and experiment invalidation are separate
from already-committed historical truth. New execution still requires the full
current eligibility proof, and the ordinary service remains fail closed.

## Reviewed operator command

From an approved installed release, using the existing protected environment:

```sh
python -m hope.api.paper_recovery \
  --migrations-dir /opt/hope-operations/current/migrations \
  --run-id 00000000-0000-0000-0000-000000000000
```

The UUID above is a format example, not an existing run or permission to act.
Replace it with an independently reviewed durable job ID from the incident.
Repeat `--run-id` for up to 32 distinct canonical UUIDs. There is no automatic
discovery, selection by loose prefix, fresh scheduling, time override, resume,
quarantine, schema migration or factory argument. An absent/failed/partial run
or a generic legacy run without certified receipts is rejected, not repaired.
This command is not installed as a periodic recovery timer.

The CLI verifies exact migration inventory/checksums before any recovery. It
uses bounded connection/statement/lock waits and prints a fixed sanitized error
without database URLs, payloads or exception text. A JSON VERIFIED result with
selected IDs is printed only after the outer commit acknowledgement. Missing
configuration/invalid selection returns 2; recovery/startup/commit failure returns
1; verified committed recovery returns 0. Lost acknowledgement is not success:
review durable state and safely retry the same explicit selection.

## Durable contract

The API consumes at most 33 values and accepts at most 32 unique UUIDs. It
requires an aware clock and the existing READ COMMITTED/RUNNING control proof.
HALT is never bypassed or resumed. Control precedes canonical ordered job locks.
Each durable job identity is independently reconstructed from its key and time.
All selected historical authorization/outcome receipts must match the original
classification, SUPPORTED research variant, immutable configuration hash,
market, sealed/PIT provider, universe manifest, actual decision-input hash and
configured latency. Filled receipts also match the actual portfolio and initial
cash. No current research validity or current PIT price is substituted for the
stored execution history.

Completed CLAIMED owners reconcile first, using the existing topology, execution
lineage, accounting replay verification, terminal transition and audit proof.
SUCCEEDED owners then use M2's existing ordinary-completion authenticator,
including recovery of a missing audit receipt. This writes observations and
valid completion only; it never reconstructs economic state. A successful result
distinguishes reconciled CLAIMED IDs from authenticated SUCCEEDED IDs, not new
trading permission. Earlier successful receipt checks are idempotent.

Every proof and write shares a batch savepoint. A later failure rolls back all
batch changes even when the caller catches it and commits other work. The API
caller owns the outer transaction; its return is provisional until commit.
Backend loss before that commit leaves no durable audit/terminal changes and
supports retry. Partial/corrupt economic history remains an incident requiring
separate review; this tool must never delete claims, overwrite evidence, reset
portfolios or fabricate missing effects.

## Operational acceptance

No database migration, new permissions, AWS activation or deployment is performed
by this batch. Use existing worker/reconciliation permissions; do not grant a
dashboard write rights. Before approved production use, stage a genuine incident
selection, independently compare cash/positions/P&L, exercise HALT/retry/outage,
confirm no additional orders/fills/applications, and retain JSON/CI/audit evidence.
AWS operational acceptance remains pending; local synthetic fixtures prove
engineering contracts only. Deadline readiness still requires real providers,
eligible research, reviewed operating configuration and approved host acceptance.
