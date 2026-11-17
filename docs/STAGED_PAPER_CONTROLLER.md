# Staged PAPER controller and readiness backlog

## Verified integration baseline

PR #82 merged at `860dbba7baf9867b49744f580a6f0fcbaad7fb70`.
Post-merge CI [#1645](https://github.com/amitgogateag-stack/hope/actions/runs/42475106909)
passed PostgreSQL 16 and 17: 4,574 tests each, 90.68% coverage. Issue #5's
authoritative marker matches that exact commit/run. No open PRs existed when
this batch started. The only open issues were the CI marker and repository
connection issue. `day29-green-baseline` remains
`7e9d341742818a97accce3e2301b1c7f96596795`.

| Category | Current evidence or prerequisite |
| --- | --- |
| Implemented and tested | Runtime/package/build/image attestation, publisher fencing, financial idempotency and recovery, signed deployment/provider/data/rollback contracts |
| Implemented but operationally unverified | Host/container namespace and process bindings, systemd identity, approved source/runtime mounts, deployment receipts |
| Missing engineering at baseline | Durable staged controller; an integrated operator preflight and staged process/receipt handoff |
| External dependency | Reviewed real India/USA provider/factory/datasets/calendars, independent signers/keys/policy, actual host observations |
| Requires authorization | Host access, deployment, systemd changes, database changes, operational PAPER execution, activation and merges |

This batch addresses durable verification orchestration. Code readiness is not
AWS operational readiness. No host was contacted and no service was activated.

## Controller boundary

`hope.api.paper_controller` has no deployment, signing, factory import, migration,
service-start or execution action. It calls the existing `inspect_deployment`
collector, pinning the approved release, immutable receipt digest and factory.
Every independent deployment gate must pass. Missing, expired, revoked or
contradictory evidence remains BLOCKED. This controller does not issue receipts,
renew approvals, select a provider or create real-data acceptance evidence.

The lifecycle is `STAGED -> VERIFYING -> VERIFIED | BLOCKED`, with `HALTED` a
terminal latch. Retry/recovery can re-enter VERIFYING from BLOCKED. VERIFIED is
verification only: it never means PAPER_READY or PAPER_RUNNING. Those states
require separately authorized activation and actual financial/worker evidence.

The controller HALT prevents further verification/admission through this
controller. It does not stop an already running service or mutate the financial
HALT state. Operators must use the existing independently authorized PAPER HALT
procedure for that purpose. There is no controller HALT-clear operation.

## Durable ownership, recovery and rollback boundary

Provision a new private state directory explicitly. Initialization exclusively
creates that directory and fsyncs its parent before publication. Existing,
missing, corrupt and legacy journals are never adopted or overwritten. An
interruption after mkdir may leave an incomplete directory: investigate it;
do not delete/reinitialize it to manufacture readiness. A new release/receipt
requires a separately reviewed new controller directory; a journal's plan is
fixed for its lifetime.

The established `publisher_lease` owns `state.json` for the entire operation.
Rejected controllers do not publish. Released, replaced or inherited leases
cannot write over a successor. Each write checks the previous journal and lease,
then uses the existing file-fsync/atomic-replace/directory-fsync writer. A failed
publication poisons the in-memory controller because rename may have occurred
before fsync failed. Never retry using an uncertain in-memory state.

The journal records VERIFYING durably before inspecting evidence. SIGKILL or
restart retains that intent. Reopening it records BLOCKED/RECOVERY_REQUIRED;
reopening VERIFIED records BLOCKED/REVERIFY_REQUIRED. Neither recovery path
reuses an old success. A new explicit verify call must inspect all evidence.
The shared activation fence covers inspection through durable verification
commit, so a cooperating release activation cannot split those boundaries.

Request digests correlate repeated verification attempts. A repeated request
always performs fresh verification; it never replays cached success. HALT is
idempotent. This controller performs no financial effects: exactly-once trading
remains the responsibility of the existing durable PostgreSQL claims and gates.

The last 32 events retain strict sequence/time/transition checks and a hash
chain. This detects bounded corruption, not malicious restoration of an entire
old journal by its privileged owner. Independent current signatures, policy,
process/runtime identities and receipt expiry remain mandatory even after a
whole-file rollback. A journal never substitutes for signed rollback authority.

VERIFIED expires at the earlier of 60 seconds and deployment acceptance expiry.
Read-only inspection revalidates the current deployment contract and matches
its entire safe evidence digest, rereads the unchanged journal, and uses the
activation fence. Stale or newly revoked proof cannot stay green. Clock reversal
fails closed. No background approval renewal is installed by this batch.

## Interfaces and safe operation

Use only a reviewed interpreter/source. Example commands below operate on an
explicitly approved **local disposable directory**; provisioning a real host
directory or running a host probe requires separate authorization.

```text
python -m hope.api.paper_controller initialize --directory <new-private-directory> --release <40-hex-SHA> --receipt <64-hex-digest> --factory <reviewed.module:function>
python -m hope.api.paper_controller verify --directory <directory> --root <approved-operations-root> --request <64-hex-request>
python -m hope.api.paper_controller inspect --directory <directory> --root <approved-operations-root>
python -m hope.api.paper_controller halt --directory <directory> --request <64-hex-request>
```

The Python API accepts the established deployment inspector with an explicitly
provided read-only DB host probe. The default CLI does not load DB credentials;
without a read-only connection, PostgreSQL compatibility remains unverified.
It must not silently bypass that gate. No receipt is created by these commands.

The optional PAPER CLI `--controller-directory` requires `--deployment-receipt`.
The existing operational guard requires fresh matching controller verification
at startup and its normal cycle checks, after source/runtime/image/provider
validation. It reuses the fresh deployment result under the existing activation
lease. It is an additional admission check, never execution authority and never
a replacement for transactional financial HALT/risk/reconciliation checks.
Legacy invocations without this optional gate retain their established behavior.

`GET /controller.json` projects only the fixed `/var/lib/hope-controller` journal
against `/opt/hope-operations`. It has 10 fixed public fields: schema, state,
code, release/receipt hashes, sequence, retained count, observation/expiry times
and `execution_authorized:false`. No factory name, filesystem path, raw audit
events, process metadata, provider output or exception text is exposed. It never
accepts a client path or a write action. The dashboard endpoint may truthfully
remain BLOCKED when its least-privilege role lacks mandatory host evidence.
Directories must be root/current-user-owned, canonical and not writable by
group/world; no native Windows production support is claimed.

## Next dependencies in order

1. Run the [unified read-only preflight](PAPER_READONLY_PREFLIGHT.md) after explicit
   host authorization; it includes non-mutating migration compatibility and
   retains every independent absent-evidence gate.
2. Engineer and test the staged process/receipt handoff in disposable Linux:
   exact process identity, approved read-only bindings, signed receipt provision,
   restart/revocation and rollback, without automatic activation/signing.
3. Independently accept a real provider/factory/dataset/calendar for both markets;
   synthetic fixtures establish engineering behavior only.
4. Obtain explicit host-access authorization for actual read-only preflight.
5. Obtain separate deployment/database/service/PAPER activation authorizations,
   then collect real multi-session observations and an operational GO/NO-GO.

No real-data integration, AWS operational acceptance, deployment, activation,
database change, strategy/risk change or LIVE capability is claimed here.
