# M5 continuation: publisher ownership and controlled EC2 contract

This extends PR #82; it performs no AWS connection, deployment, host/systemd
mutation, production database write, credential change, merge or strategy/risk
change. PAPER-only and LIVE-disabled gates remain in place.

## Publication ownership defect

Previously a rejected competing worker entered the service exception handler
and wrote UNAVAILABLE over the active worker's CURRENT status. CLI startup
failures could do the same. Startup failure publication now independently
acquires the same exclusive publisher lease. Acquisition rejection never writes
health/history. After acquisition, corrupt-history failure may invalidate health
while ownership remains valid. After lease loss, the old publisher cannot write,
reacquire or overwrite a successor. Handles also reject use after context exit
and inherited use from another PID. A private lock header identifies the actual
kernel process incarnation and an opaque per-acquisition token. The lock uses
0640, matching the existing health-file operations group: monitoring may read
but never write it. Permissions are changed only after acquisition. Existing
hope-paper/hope-dashboard roles already share hope-operations; no systemd or
host group change is performed.

Every public run_service instance, including research callers without a runtime
guard, owns its health publication. Guarded workers additionally retain the
existing bounded history. Low-level atomic file publication remains a reusable
primitive; production call sites route through ownership checks.

New raw service health uses protocol 2 and binds the owner token and kernel
identity. The HTTP projection stays protocol 1 for compatibility. A CURRENT
protocol-2 heartbeat must match a held kernel lease and its live process before
it can be projected into readiness. Terminal health remains inspectable after
lease release. Legacy protocol-1 observations retain existing validation.

The genuine PostgreSQL certified child test now holds worker A CURRENT, rejects
worker B and a failing CLI, checks A's health/history bytes unchanged, checks
one claim/effect/NO_TRADE decision, then verifies A's normal STOPPED publication
and clean idempotent restart. Native multi-process tests separately cover
ownership rejection, lock replacement, successor publication and released-handle
rejection. These tests do not relax the existing ten-second shutdown bound.

## Independently approved integration graph

The new read-only deployment_contract module composes existing runtime/image,
source Git, process identity, bounded capture and AWS readiness checks. It never
creates approval keys, signs an artifact, imports a provider factory or changes
host state. The default root is /opt/hope-operations when used by the dashboard.

A release selector deployment-receipts/<SHA>.ref contains exactly the SHA256 of
an immutable deployment-receipts/<digest>.json plus detached .sig. Receipt
schema hope.deployment-receipt.v1 binds release/image/runtime identities, host
and boot hashes, full container identity, exact command/interpreter/process
hashes, PostgreSQL 16/17, minimum systemd, provider and rollback receipt hashes,
policy-generation floor and an expiry of at most one hour. Production scope
must be PRODUCTION_PAPER. The module never updates the selector or receipt.

Four independent root-installed roles approve deployment, provider,
real-data acceptance and rollback. Their Ed25519 keys must differ from each
other and image/policy keys; current signed image policy revocation applies to
all four roles. Image approval may explicitly use PRODUCTION_PAPER only under
an independently supplied signature. Image construction remains an unsigned
DISPOSABLE_LAB_ONLY candidate and cannot promote itself. A lab image cannot
satisfy a production deployment receipt.

Provider contract schema hope.paper-provider.v1 pins current release, reviewed
factory module/function/source hash, explicit dataset and calendar hashes,
India/USA markets and a bounded approval lifetime. Source Git and bytes are
verified without importing the module. Separate signed real-data acceptance
binds the same provider/dataset/calendar, current complete PIT feed or verified
session closure, scheduler, risk, reconciliation and LIVE=false. Its maximum
lifetime is 120 seconds. Synthetic/lab scope cannot satisfy this contract. The
existing certified research, market-data, accounting and risk gates still
independently validate every real execution; these declarations cannot replace
those checks or admit an unvalidated strategy.

Rollback receipts are content-addressed, expire, identify a different prior
release, and bind its exact authenticated shared/release runtime manifest and
archive bytes. Prior source Git, package and build proofs must verify. No
rollback is executed by this collector.

## Fresh host and container observations

Host discovery hashes public machine/boot/OS identifiers, checks the actual
Python version and fixed systemd version output. PostgreSQL major is UNKNOWN
without a supplied read-only database connection. The probe checks transaction
read-only before SHOW server_version_num and never changes transaction mode.

Container inspection invokes only fixed docker/podman binaries and seven
allowlisted public fields. It never requests environment, mounts, unrestricted
inspect JSON or credentials. Mutable image tags, privileged/writable-root
containers, host networking, invalid PIDs or contradictory config digests fail.
The OCI manifest binds the engine's config digest; /proc kernel executable
inode/content, exact argv hash and boot/start identity bind the running process.
Host, process, container, receipt selector, policy, provider acceptance and
rollback evidence are rechecked before returning VERIFIED.

A reviewed PAPER CLI can pin --deployment-receipt <SHA256>. It then binds that
exact receipt and approved factory at startup and before execution checks. Its
existing configured database connection is opened lazily only after source,
runtime, image and provider approvals pass; the host probe uses a fresh
read-only transaction. Legacy launch behavior is preserved when no receipt is
requested. Neither diagnostics nor receipt approval activates a service.

The read-only dashboard adds /deployment.json and /publisher.json, showing
VERIFIED/BLOCKED/UNKNOWN, receipt/image evidence, host/container/provider/rollback
stages, owner incarnation and fixed blockers. Observations age in the browser;
no lab report or dashboard response authorizes execution or promotes READY.

## Outstanding controlled integration requirements

No real provider, production factory, production dataset, trust key, deployment
receipt, running EC2 container or production acceptance was supplied or created.
Actual controlled integration requires independently reviewed real artifacts,
root-installed role keys/current policy floor, approved source and real-data
factory configuration, durable current/rollback artifacts, read-only host and
DB access, and an independently observed running-container/boot/process receipt.
A controller must coordinate the startup receipt with the observed process;
this batch does not deploy or silently renew that authority. All absent or
contradictory evidence remains UNKNOWN/BLOCKED.
