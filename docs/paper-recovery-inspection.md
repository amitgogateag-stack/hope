# Read-only PAPER recovery inspection

The execution/reconciliation APIs require RUNNING control and authenticated
durable evidence. Observation is a separate operation. An operator can inspect
up to 32 explicit canonical run UUIDs, including while HALTED, without granting
permission to recover, resume, retry, quarantine or execute trading work.

Using the existing approved PostgreSQL configuration and preferably a dedicated
SELECT-only role, run:

```sh
python -m hope.api.paper_recovery_inspect \
  --run-id 00000000-0000-0000-0000-000000000001 \
  --migrations-dir /reviewed/release/migrations
```

The example UUID is a placeholder; supply reviewed actual identities. The command
uses HOPE_DATABASE_URL from the existing protected environment. Do not paste a
connection string into tickets, command arguments, or logs. This change creates
no role, grants no permission, deploys no endpoint and does not modify host files.

The command validates the exact installed schema, then performs SELECT queries
in a PostgreSQL READ ONLY, REPEATABLE READ transaction. The inspection function
independently verifies both transaction properties before observing run state.
Connect, statement, lock and pool waits are bounded. There is no automatic
discovery or unbounded selection; generators are consumed at most 33 times to
reject overflow. Aggregate effect counts are classified using at most two
occurrences per effect kind rather than expanding counts into memory.

Output schema `hope.paper-recovery-inspection.v1` includes:

- Observed RUNNING/HALTED control state from the same transaction snapshot.
- Selected run IDs in the requested order; MISSING, OUT_OF_SCOPE, INVALID or a
  recognized PAPER lifecycle state.
- Shared existing effect-topology classification and counts by known effect
  kind, plus presence of authorization, certified outcome and reconciliation
  receipt rows. Unknown effect kinds are contradictory and not echoed.
- Fixed `proof: NOT_VERIFIED`, `recovery_authorized: false`, `paper_only: true`
  and `live_enabled: false` markers.

Presence and topology are observations. COMPLETE does not authenticate execution
lineage, accounting balances, scientific evidence, eligibility or receipt payloads.
A present receipt is not a verified receipt. MISSING does not authorize creating a
job. Do not use this output as scheduler admission, dashboard CURRENT health or a
substitute for the existing authenticated recovery APIs. A later transaction can
observe newer state; this command holds no lifecycle or control locks.

No job keys, signal identifiers, inputs hashes, payloads, principals, reason text
or driver exception details are returned. Schema/connection/query failures emit a
fixed unavailable error and nonzero exit without partial observations. JSON is
published only after successful transaction completion. An observation under HALT
does not relax the existing HALT or incomplete-claim resume restrictions.

Tests use genuine PostgreSQL for HALTED observation, FILLED/NO_TRADE/RISK_BLOCKED,
explicit selection bounds, read-only enforcement and concurrent peer commit.
The peer can terminalize a job and HALT control while inspection is in progress;
the first result remains one consistent snapshot and the next sees the new state.
All policy and database guards remain enabled. These synthetic fixtures are not
production evidence, and AWS operational acceptance remains separate operator work.
