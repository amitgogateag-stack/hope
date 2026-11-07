# Runtime identity and release integrity

Git identity authenticates source. It does not authenticate a Python interpreter,
its virtual environment, dependencies, import hooks, or the process listening on
the private dashboard port. These are separate mandatory checks.

## Independent approval boundary

Shared `/opt/hope-operations/venv` and release-specific
`/opt/hope-operations/release-venvs/<sha>` environments each require an approved
external manifest at `runtime-manifests/<sha>-<shared|release>.json`. The directory,
manifest, runtime, and release source must be root-owned, without group/world
write permission. Manifest files must be regular, unlinked files. The dashboard
service account needs read access to the manifest and inventoried runtime files.
Credentials and environment files are not inventoried or returned by diagnostics.

`hope.api.runtime_integrity.build_runtime_manifest` is a read-only candidate
inventory API. Generating a candidate on an existing host does **not** approve
that runtime. Activation never generates, installs, or updates its own authority.
A separately reviewed builder/operator must compare the candidate with the
trusted, reproducible installation, approve its dependency and interpreter
provenance, and stage it independently. Those host actions require separate
authorization and have not been performed by this engineering batch.

The candidate records every regular runtime file's SHA256, permitted symlink
targets, the resolved interpreter and its SHA256, distribution metadata, and the
release's tracked constraints digest. Critical runtime distributions must exist
and all constrained installed distributions must match their pins. Constraints
are version pins, not a wheel provenance signature; they cannot independently
approve package bytes. The approved external file inventory supplies that byte
authority. Neither a matching commit nor locally generated METADATA is enough.

Only internal links and canonical Python aliases to a trusted executable are
accepted. External import paths and unapproved `.pth` startup hooks are rejected.
Existing reviewed `distutils-precedence.pth` remains compatible, with its exact
bytes still covered by approval. System-site packages are disabled. Python 3.13
is required for the selected HOPE runtime.

## Bootstrap, activation and rollback

The operator dispatcher starts `/usr/bin/python3 -I -B -S` using the OS trust
anchor, reviewed release source, and a fixed nonexistent bytecode-cache prefix.
Its source/Git/runtime validation uses only the standard library. Only after
static approval does it execute the selected control venv with a minimal
environment, then reauthenticate the pinned manifest before importing activation
dependencies. `HOPE_DASHBOARD_CONTROL_RUNTIME` selects `shared` (default) or
`release` for this control process; `HOPE_DASHBOARD_RUNTIME` separately selects
the proposed dashboard runtime. Neither accepts an arbitrary path. An unavailable
shared runtime can therefore be diagnosed through an independently approved
release control runtime. OS Python must be at least 3.9 and remains an explicit
host trust anchor. This does not defend against a compromised root or kernel.

Source checks reject ignored executable bytecode and cache directories. Existing
release Git authentication, source trust checks, atomic pointers, exclusive
activation ownership, PAPER-disabled checks, database compatibility checks,
health verification and rollback remain authoritative. New v3 journals pin both
target and previous runtime manifests. Preflight rechecks both identities after
the database/listener probes, before mutation. Interrupted activation fails
closed. Rollback authenticates the prior runtime before restarting it.

Legacy v1/v2 journals remain readable with `UNVERIFIED` runtime evidence. They
cannot produce green readiness. New activation requires independently approved
runtime manifests for both target and prior runtime. When restoring legacy code,
the journal is downgraded to its supported schema, while the private backup
retains the manifest binding for interrupted rollback recovery. No compatibility
path silently upgrades unbound evidence to VERIFIED.

## Read-only diagnostics and bounds

`python -m hope.api.runtime_integrity <sha> --runtime shared|release` is a bounded,
read-only diagnostic for the fixed operations root. Run it from independently
trusted source/runtime; it is not an installation or approval command. The
dashboard `/runtime.json` exposes only fixed schema, state/code, release SHA,
mode, opaque digests, file count and `execution_authorized: false`. Paths,
arguments, credentials and exception text are excluded. States distinguish
`VERIFIED`, `UNVERIFIED`, `MISMATCH`, and `BLOCKED`.

Attestation permits at most 20,000 entries, 512 MiB total runtime file bytes,
128 MiB per file, 4 MiB manifest bytes and three seconds. Streaming reads and
enumeration are bounded. An oversized or slow environment is blocked for review;
bounds are not automatically relaxed. Previously hashed content may be cached
only while every entry's inode/ctime/permissions and constraints/manifest storage
identity remain unchanged. The cache is memory-only and bounded to eight keys.
Restart therefore requires a full first attestation.

AWS readiness independently compares the approved runtime with a stable systemd
PID, boot/start identity, kernel executable, exact selected venv command and
private loopback socket. It repeats runtime evidence at the observation's close;
process restart or same-byte file replacement invalidates the observation. HTTP
self-report alone cannot make this check pass. Dashboard READY requires current
persisted snapshot evidence and verified runtime identity for its deployed SHA.
HALTED, STALE, BLOCKED and RECOVERY_REQUIRED retain their existing safety meaning.

No diagnostic authorizes execution, activates PAPER, changes risk, or establishes
AWS deployment readiness by virtue of GitHub CI alone. LIVE remains disabled.
