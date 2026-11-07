# M4: reproducible release and autonomous PAPER acceptance

This package builds disposable release candidates and exercises acceptance. It
does not authorize or perform AWS deployment, host configuration, service
activation, real broker orders, production credentials use or production DB
writes. LIVE stays disabled; strategies, sizing and risk limits are unchanged.

## Trust and review sequence

The merged M3 controls remain authoritative: clean pinned Git source,
root-owned external manifests, wheel RECORD/content verification, Ed25519
approvals, activation journal v3, atomic pointers, shared activation leases,
durable scheduler recovery, candidate authorization and fail-closed PAPER gates.
M4 extends these mechanisms instead of adding a second deployment state machine.

1. An independent builder authority approves canonical `hope.build-input.v1`
   bytes and a detached Ed25519 signature. Inputs identify release SHA, builder
   image/policy digest, exact Python executable SHA256, OS-release SHA256, target
   platform, fixed archive epoch, logical deployment root, wheel filenames,
   artifact SHA256/name/version records and requested extras. An approved builder
   image digest is an externally reviewed identity, not inferred from a label.
2. A marked disposable Linux/Python 3.13 builder validates that approval using
   the existing trust/file checks and shared signature verifier. It verifies
   the actual interpreter and OS-release bytes before executing the interpreter.
   Source must be a clean immutable checkout at the approved SHA, with no ignored
   executable caches. Every approved wheel is verified before installation.
3. Two separate venvs are created with `--without-pip --copies`. A deterministic
   offline installer copies only validated wheel payloads, generates declared
   entry points and consistent installed RECORDs, and never runs pip, package
   install hooks, dependency resolution or network operations. Supported layouts
   retain the conservative purelib/platlib/entry-point policy; unknown layouts
   fail closed. No signing key is read by the builder.
4. Runtime activation scripts are omitted because services use explicit Python.
   Non-semantic `pyvenv.cfg` command/prompt metadata is removed. Generated entry
   points and interpreter manifest paths use the approved logical deployment
   prefix, so separate physical work directories do not change their bytes.
   Package/source/executable bytes are never normalized or rewritten.
5. Each build emits a deterministic USTAR archive, runtime inventory, package
   provenance candidate and CycloneDX 1.6 SBOM. Entries are ordered; uid/gid and
   owner names are canonical; mtimes use the approved epoch; executable modes
   are explicit. Dependency components carry wheel hashes and resolved target
   dependency relationships. Source identity has an independent SHA256 inventory.
   All four output hashes must match across both builds.
6. The result is **REPRODUCIBLE and approved=false**, never an approval or READY
   assertion. A separate release authority must independently review the inputs,
   outputs, SBOM and two-build comparison and sign a `hope.release-approval.v1`.
   Production code has no key-generation, signing or self-approval operation.

The builder is executable as:

```text
python -B -m hope.api.release_builder --source CLEAN_CHECKOUT --work MARKED_TEMP_ROOT \
  --inputs INPUTS_JSON --signature INPUTS_SIG --public BUILDER_PUBLIC_KEY \
  --inputs-sha256 REVIEWED_INPUT_DIGEST --python REVIEWED_PYTHON_313
```

All paths must satisfy ownership/mode/no-follow checks. Work must be canonical,
strictly below the OS temporary root, with `.hope-builder` containing
`hope.release-builder.v1` plus newline. Authority/source inputs must be outside
work. Builds `a` and `b` must not already exist; interrupted work is retained for
inspection, not automatically erased/reused. Artifacts remain bounded by the M3
wheel/runtime limits; release archives have a 128 MiB bound. Venv creation has a
60-second timeout. Failed CLI operations return only a bounded BLOCKED result.

## Runtime binding

Runtime manifest v3 adds `release_build_sha256` to the v2 package binding.
For release SHA/mode, external authority under the operations root comprises:

| Path | Meaning |
| --- | --- |
| `release-builds/<sha>-<mode>.json` and `.sig` | Independently signed release approval |
| `release-builds/<sha>-<mode>.inputs.json` and `.inputs.sig` | Independently signed builder inputs |
| `release-builds/<sha>-<mode>.sbom.json` | Approved dependency/source SBOM |
| `release-builds/<sha>-<mode>.tar` | Immutable approved release archive |
| `runtime-trust/builders-ed25519.pub` | Independently provisioned builder authority |
| `runtime-trust/releases-ed25519.pub` | Independently provisioned release authority |

Existing `artifact-provenance`, wheelhouse, artifact trust key and external
runtime manifest paths remain required. Builder and release signatures, runtime
inventory digest, package descriptor, SBOM components/edges and archive hash must
agree. Archive hash caching is metadata-bound and bounded; signature and closing
identity checks still run on each attestation. Raw exception text and authority
file contents never enter public runtime diagnostics.

Legacy inventories remain readable with build UNVERIFIED. New target activation,
production PAPER cycle admission and dashboard/AWS READY require verified build
evidence. Prior compatible package-verified code can still be checked for explicit
rollback; absence of its M4 build proof never silently makes it operationally
READY. No approval/key/runtime is staged on a real host by this batch.

## Disposable acceptance and recovery

`release_acceptance.stage_lab_release` checks an exact archive SHA256 and stages
only below a canonical marked temporary root. `.hope-acceptance` must contain
`hope.operational-acceptance.v1` plus newline. It clones a separately verified
local Git source without hardlinks or hooks, validates every regular tar member,
rejects traversal, unsafe modes/links and authority injection, restores only
declared internal runtime links, then verifies the staged Git identity again.
It does not create approval manifests or accept an unsigned runtime.

The archive contains tracked release files, runtime files and candidate evidence;
Git authority is preserved by the independently verified checkout rather than
embedding mutable Git metadata in the deterministic archive. Partial staging is
left unapproved/blocked. Existing roots cannot be overwritten implicitly.

The production activation/rollback machine remains the mechanism for switching
and recovering releases. Existing native termination, journal corruption,
concurrency and rollback suites are retained. M4 adds real Python venv/process
identity checks, exact archive recovery/staging and build/SBOM rejection. A
process observation binds kernel executable/argv and boot/start identity; PID
reuse or restart changes the token. Process start alone is never acceptance.

`acceptance_observation` requires current matching release/runtime/build proof
and explicit database, process, scheduler, market, reconciliation, risk and
LIVE-disabled observations. Missing, contradictory, future or older-than-five-
minute evidence blocks. CLOSED is a declared calendar outcome; it is not a
fabricated opportunity. Lab observations always retain DISPOSABLE_LAB_ONLY and
execution_authorized=false. Their adapters are test resources, not production
database or broker approvals.

Real PostgreSQL regression exercises India and USA calendars, half-open session
boundaries, IANA US DST, an explicitly synthetic omitted holiday, multiple
sessions, process/pool restart and durable exactly-once NO_TRADE. Existing suites
retain stale/missing/duplicate/out-of-order ingestion, broker simulation,
partial acknowledgements, interrupted transactions, accounting/risk/HALT and
unauthorized LIVE coverage. No synthetic fixture is real trading evidence.

## Read-only dashboard and operational prerequisites

Runtime diagnostics now carry build state, approved build digest and opaque
builder identity; missing proof blocks READY. The dashboard also reads bounded
`acceptance-result.json` via `/acceptance.json`, ages observations after five
minutes and shows explicit blockers/actions. This historical lab result never
feeds operational readiness and never authorizes execution.

Source/PR CI is engineering evidence only. Real controlled operational acceptance
still requires independent image/builder/artifact review, public trust-key and
signature provisioning, the actual reviewed factory and real point-in-time
inputs, scoped PostgreSQL compatibility/permissions, resource verification and
separate approval of any host or unit changes. AWS remains untouched/unverified.
