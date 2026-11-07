# M5 offline image and recovery acceptance

This batch produces unsigned **DISPOSABLE_LAB_ONLY** OCI image layouts. It does
not authorize deployment, assert AWS readiness, or activate a PAPER worker.
LIVE stays disabled. Production source, runtime, database, authorization, risk,
and reconciliation gates continue to apply independently.

## Offline image candidate

Run `python -m hope.api.release_image --help` using the reviewed control source
and trusted OS Python. The builder requires a new marked directory beneath the
OS temporary directory (`.hope-image-builder` containing
`hope.image-builder.v1\n`). Inputs and public authorities must be outside it.
It emits `a/`, `b/` OCI layouts and `candidate.json` only after equality checks.
No registry, daemon, network, pip, package hook, signing key or cleanup command
is used. Existing directories are never reused.

Independent Ed25519 signatures approve the base document, builder inputs and
M4 release approval. All three keys must differ. The image builder reuses the
release builder input validator and requires signed image-compatible v2 inputs. The base document binds a bounded uncompressed POSIX tar to its full
file/mode inventory, actual Python 3.13 executable bytes and `/etc/os-release`.
The approved builder platform must match the OS architecture. Archives reject special files,
symlinks, hardlinks, traversal, duplicate names, whiteouts, writable/setuid
modes, PAX overrides and excessive files/bytes. OS inputs must be regular-file
materializations; distribution symlinks must be resolved by a separately
reviewed base preparation process, never by this builder.

The release approval binds Git SHA, original archive, SBOM, package provenance,
source inventory, runtime inventory, builder identity and OS/Python hashes.
Every source/runtime byte is checked. The candidate embeds the approved
release/base documents and evidence, but no signing authority. Image config
requires non-root UID 65532, PAPER environment and LIVE=false. There are no
production credentials, current pointers, deployment receipts or trust keys.

Signed `hope.build-input.v2` permits only `/usr/bin/python3.13` as the logical OS
Python. It normalizes generated venv `home` and `executable` metadata; interpreter
and package bytes remain unchanged. Existing v1 host builds retain their
original behavior. PAPER bootstrap accepts an explicit `--runtime-mode release`
and carries it through pinned reauthentication; the default remains shared.
An invalid selector never reaches authentication or process execution.
The image verifier reconstructs and hashes the original
release archive to reject contradictory evidence even when an image signature
and every OCI content address are valid.

## Image approval, expiry and revocation

A separate operator must independently review the candidate and sign an exact
`hope.image-approval.v1` document: candidate fields, schema/state changed to
`hope.image-approval.v1`/`APPROVED`, plus integer `issued_at` and `expires_at`.
Scope remains DISPOSABLE_LAB_ONLY and execution_authorized remains false.
Approval lifetime cannot exceed 24 hours. There is no signing API in production
code, and no automatic production trust authority.

A distinct policy key signs `hope.image-policy.v1` with positive integer
`generation`, integer `issued_at`/`expires_at` (maximum 24 hours), and bounded
`revoked_images`/`revoked_keys` SHA256 lists. Revocation covers image signing
keys and all three upstream provenance keys retained in the image approval. Key revocation fingerprints the
Ed25519 SPKI DER, not PEM whitespace. Verification rechecks signatures, expiry,
policy and all OCI blobs on every call, including expiry at the closing
observation. Replacement during construction/attestation fails closed. Callers may pin `minimum_generation`;
policy generation is not a durable anti-rollback counter by itself. Production
adoption must supply an independently maintained minimum generation and clock.

Dashboard `/image.json` reads only fixed files beneath the snapshot directory's
`image-evidence/`: `oci/`, `approval.json/.sig`, `images-ed25519.pub`,
`policy.json/.sig`, `policy-ed25519.pub`. Missing, corrupt, revoked, mismatched
or expired evidence returns a fixed UNVERIFIED observation with no private
paths or exception text. The browser visibly ages approval expiry. Image
observations are never inputs to READY.

## Restart and recovery history

Guarded production service instances hold an exclusive, private lifetime flock
for the health-file publisher. A bounded 32-event hash chain records starts,
completed poll outcomes and stops across process restarts. Publication reuses
the existing fsync + atomic replacement primitive. The lease and persisted
history are checked before factory execution and before each scheduled claim.
Unexpected modification/deletion, a substituted lock, corrupt/partial history,
non-monotonic time or competing publisher blocks the worker and invalidates
CURRENT health. No automatic history repair is performed.

The chain detects corruption within its retained window; it is not a signed,
privileged-rollback-proof financial journal. A missing history on initial
adoption is allowed for legacy compatibility and does not claim historical
recovery. Database claims/effects and existing reconciliation/HALT remain the
financial authority. `/history.json` exposes only bounded aggregate counts and
latest state, expires after five minutes, and never certifies recovery or READY.

## Validation and remaining deployment gates

Native tests build actual CPython 3.13 environments twice from offline wheels,
construct OCI layouts, verify independent ephemeral lab signatures, and run the
image-specific interpreter/venv as UID 65532 inside a disposable rootfs and a
network namespace. Linux CI must have sudo/unshare/chroot; inability is a test
failure, not a skip. These fixtures contain explicitly synthetic packages and
source. They demonstrate image/runtime compatibility, not trading correctness.
The full PostgreSQL 16/17 suites separately exercise real durable PAPER flows,
India/USA sessions and DST, simulated orders/cash/positions/risk, crash/restart,
duplicate claims, lost acknowledgments, database failures and reconciliation.
M5 adds repeated guarded failures, lost post-commit acknowledgement, unattended
restart, corrupt-history denial and explicit validated lab restoration.

A production image requires an independently approved real OS inventory,
reviewed real dependency inputs, Git metadata compatible with the existing
source gate, external read-only trust mounts, current policy generation,
container-engine/host identity and deployment receipts, real-data PAPER
acceptance and separate deployment approval. None is inferred from lab results.
No AWS operation, service activation, production database write, risk change or
frozen-baseline modification is part of this batch.
