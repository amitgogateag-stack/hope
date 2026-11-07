# Reproducible runtime provenance and controlled PAPER acceptance

This engineering package changes repository code and disposable tests only.
It does not install a release, stage a trust key, activate a service, contact a
broker, modify AWS, or authorize PAPER execution. LIVE stays disabled.

## Independent authorities

Git authenticates source. Runtime manifest v1 authenticates an externally
approved inventory, but cannot establish package artifact provenance. It remains
readable with `provenance_state: UNVERIFIED`; it cannot make new activation,
bootstrap, autonomous PAPER admission or dashboard READY pass.

Runtime manifest v2 adds `package_provenance_sha256`, the SHA256 of the **exact
canonical bytes** of a signed descriptor. Activation journal v3 already pins the
runtime manifest, so its target/prior pins also transitively pin provenance.
No competing activation journal or rollback implementation is introduced.

For each reviewed release SHA and mode (`shared` or `release`), the external
deployment root contains:

| Path below operations root | Authority |
| --- | --- |
| `runtime-manifests/<sha>-<mode>.json` | Approved runtime inventory v2 |
| `artifact-provenance/<sha>-<mode>.json` | Canonical package descriptor |
| `artifact-provenance/<sha>-<mode>.sig` | Detached 64-byte Ed25519 signature |
| `runtime-trust/artifacts-ed25519.pub` | Independently provisioned Ed25519 SPKI public key |
| `wheelhouse/*.whl` | Exact immutable approved wheel artifacts |

All authority directories/files retain the existing root-owned, non-group/world
writable trust boundary. Regular files must have one link; descriptor reads are
no-follow, bounded and checked against opened/named inode metadata. Interpreter
aliases and internal library links retain the existing explicit allowlist.
Service users need read access. A signing private key is never placed in the
runtime, deployment root, repository, diagnostic output or service environment.
Key provisioning/rotation and all host staging require separate authorization.

`/usr/bin/openssl` and OS Python are explicit host trust anchors. Ed25519
verification uses a fixed executable, minimal environment, disabled ambient
OpenSSL configuration, a bounded timeout and suppressed raw output. The
descriptor's signature is verified before artifact contents are interpreted.
Root/kernel compromise is outside this boundary. A locally edited runtime
inventory or locally generated candidate cannot replace the signing authority.

## Reproducible build and independent review

Use a disposable builder with an independently approved OS/container digest,
Python 3.13 patch version, interpreter SHA256 and target architecture. The
deployment prefix must match the reviewed prefix because `pyvenv.cfg`, generated
entry points and interpreter paths can contain absolute paths. Freeze the builder
tool versions and locale. Record its immutable identity in the review evidence.

Resolve every runtime/bootstrap distribution against the tracked
`constraints/ci-test.txt`. Acquire and independently review the exact wheel
hashes/provenance, including pip/setuptools if present. Build an offline wheelhouse.
Do not approve a dependency merely because its installed METADATA claims a
version or because a download URL uses TLS. Version pins alone are not artifact
authority. This batch adds no invented package hashes to tracked constraints.

Install wheels offline, with dependencies resolved from the approved wheelhouse,
and **without bytecode compilation**. For example, a reviewed builder may use
`pip install --no-index --find-links WHEELHOUSE --no-compile -c constraints/ci-test.txt ...`.
Do not use editable installations or import paths pointing outside the runtime.
Keep service execution bytecode-free; both new PAPER bootstrap stages use `-B`.
Unknown import hooks, extra package files, cached bytecode and unapproved
generated files are blocked. Installer metadata and declared entry points have
explicitly approved hashes; arbitrary generated Python modules are not allowed.

The read-only Python APIs implement the three separate stages:

1. `runtime_integrity.build_runtime_manifest(root, sha, mode)` inventories a
   **candidate** runtime. `package_provenance.build_candidate(root, sha, mode,
   wheels, runtime_manifest=candidate, requested_extras=...)` checks actual wheel
   bytes and constructs a descriptor. These functions neither sign nor approve.
2. A separate reviewer compares interpreter, target platform, exact artifacts,
   dependency relationships, generated files and build provenance. Only that
   independent authority signs the canonical descriptor bytes. An authorized
   offline signer can use `openssl pkeyutl -sign -rawin -inkey REVIEW_KEY -in
   DESCRIPTOR -out SIGNATURE`. The runtime verifier has no signing operation.
3. Build candidate runtime manifest v2 with `provenance_digest=SHA256_DESCRIPTOR`.
   Independently approve/stage it and the detached signature/public key/artifacts.
   `attest_runtime` verifies both authorities before producing package VERIFIED.

Rebuild twice from the same approved inputs and compare artifact, installed-file,
interpreter and generated-file digests. Investigate differences; never update an
approval automatically to match the host. Candidate generation is not a clean
build assertion. Approval of the full inventory remains an explicit review step.

The descriptor records exact distribution/version/artifact SHA256, interpreter
SHA256, target platform, wheel tags, raw Requires-Dist relationships, explicit
requested extras and generated-file hashes. ZIP paths, symlinks, duplicate names,
encryption, expansion bounds and each RECORD SHA256/size are validated without
extracting or importing wheel code. Installed package bytes must match their
artifact payloads, and all installed distributions must be accounted for.

Dependency markers are evaluated by a bounded AST allowlist, never `eval`.
Target Python is 3.13; extras propagate through dependencies. Unsupported version
syntax, URLs, marker syntax, wheel layouts or platform tags fail closed. The
initial native-wheel policy supports reviewed CPython 3.13 Linux manylinux tags
and pure Python wheels; support is deliberately conservative. A compatible but
unsupported wheel needs an engineering/review change, not a bypass. No network
resolution or package installation occurs during verification.

Existing runtime bounds remain: three seconds, 20,000 entries, 512 MiB runtime
file bytes, 128 MiB per file and 4 MiB manifests. Provenance further bounds the
wheelhouse to 128 artifacts/256 MiB compressed bytes, 64 MiB per wheel and 256 MiB
expanded per wheel. Streaming/entry deadlines fail closed. File, authority and
directory identities are checked again at closure; artifact/key/signature
identity contributes to the opaque observation token. The existing runtime hash
cache never bypasses signature or artifact verification.

## PAPER launch and recovery

The reviewed PAPER unit now launches OS Python with `-I -B -S` and a fixed
nonexistent bytecode-cache prefix. `paper_release.py` authenticates source and
signed shared-runtime provenance before handing off to its Python. The handoff
pins the approved manifest and forwards only required service/database/notifier
values, without logging them. Its second stage reauthenticates before importing
worker dependencies. The database preflight still enforces the scoped PAPER
role through `operations_deployment`; it is not removed or weakened.

The production CLI verifies deployment SHA, exact interpreter/prefix, current
pointer, source, journal state, provenance and explicit LIVE=false **before
factory import**. Each polling cycle holds the shared activation lease, rechecks
before factory/claim boundaries and again at closure. The scheduler's existing
durable preflight/reconciliation, candidate authorization, stale scheduling and
market-data freshness checks remain authoritative. No stale/missing runtime can
authorize a new cycle. Embedded test/research supervisor APIs remain available;
production callers must use the guarded CLI and reviewed factory.

The heartbeat records `runtime_gate` only after a complete guarded cycle. Missing
legacy evidence is UNVERIFIED. Failure invalidates the gate; a startup failure
also attempts to replace the previous worker's CURRENT heartbeat with a bounded
failure observation. A process starting alone never establishes READY. Counters
describe this process; durable idempotency/reconciliation controls restart replay.

Unresolved claims require explicit reconciliation. Runtime, stale scheduler,
clock, market-data and reconciliation failures retain fixed error codes without
exception text. No retry repairs a partial economic effect by running a strategy
again. Existing reconciliation receipts and exactly-once effect guards remain.

## Disposable rehearsals and dashboard evidence

`deployment_rehearsal.RehearsalHost` adapts the **production** dashboard activation
and rollback state machine to a marked directory below the OS temporary root.
Units are files below that root; there is no systemctl, broker, installer or AWS
client. Signature/runtime checks use real artifacts. Explicit lab adapters supply
source, database and health observations; they cannot certify a production host.
The report is emitted only after successful health and closing identity checks.

Tests exercise first dashboard deployment over a verified shared baseline,
upgrade/repeated recovery, bad manifests/interpreters/packages, absent/contradictory
database evidence, health/LIVE/stale-snapshot failures, conflicting activation
and termination after PREPARED/config/pointer/VERIFYING/start/COMMITTED boundaries.
SIGKILL-equivalent process exit leaves real journals/leases/pointers for explicit
verified recovery. An unrecoverable case stays blocked; no simulated service is
presented as real host activity. Real PostgreSQL tests cover durable multi-session
NO_TRADE, closed sessions, process/pool restart, runtime denial before claims,
concurrency, incomplete effects, accounting/reconciliation and broker fixtures.

`/runtime.json` includes bounded package provenance evidence. Dashboard READY
requires runtime, provenance and a guarded worker cycle alongside existing
persisted health/snapshot/recovery evidence. `/rehearsal.json` exposes only the
last persisted **DISPOSABLE_LAB_ONLY** observation, ages after 24 hours and never
feeds operational readiness. `/paper-diagnostics.json` offers fixed operator
actions and scheduler/recovery diagnoses. Market freshness remains UNVERIFIED
unless explicit persisted evidence establishes it; a healthy process is not a
market-data freshness proof. Paths, signatures, private keys, wheel contents and
raw errors are excluded from public diagnostics.

Operational acceptance still requires separate approvals for installing external
authority, independently reviewing the actual factory and real point-in-time
inputs, scoped PostgreSQL read-only checks, resource/permission checks and any
host/unit changes. GitHub CI and lab rehearsal results do not establish AWS
deployment readiness. No such host actions are part of this batch.
