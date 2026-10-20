# M3 exporter installation engineering checkpoint — 2026-10-19

**Engineering candidate only. No AWS installation or activation has occurred.**

## Verified operational constraint

The reported EC2 host has an active private dashboard web unit, but the exporter
service and timer are absent. The scoped dashboard database role was reported to
have SELECT on 49/49 public tables and no table-write or CREATE privileges.
This is encouraging but does **not** prove the effective connection role, all
sequence/function privileges, database read-only transaction enforcement, or
PAPER execution isolation.

The existing M3 dashboard-only activation deliberately fails closed when the
PAPER unit is absent. Preserve this restriction. A missing unit is not proof
that no detached worker, external scheduler, or other process can trade.

## Reviewed installation contract

A separate exporter-only installer must be distinct from the existing full
operations installer, which also installs PAPER. It must:

1. Require explicit root operator approval and an independently verified
   expected shared-release SHA; verify a trusted clean release and exact
   reviewed exporter service/timer templates.
2. Reject existing units, symlinks, unknown drop-ins, unsafe template directives,
   active timers, inconsistent systemd metadata, and concurrent installation.
3. Create **only** the two missing exporter unit files, initially inactive.
   The service is expected to be **static** (no Install section); the timer
   must be **disabled**. Never start, enable, or restart PAPER.
4. Record an fsynced root-owned installation journal with content hashes and
   inode/device identity. Use exclusive creation; never overwrite host files.
5. On an ordinary failure, remove only newly created, identical, quiescent
   units. Interrupted or ambiguous journal state must block for manual review.
6. Keep snapshot refresh, timer enablement, dashboard activation, and rollback
   as separate explicit approvals. Never alter database grants, migrations,
   credentials, shared current, deployed.env, or the frozen baseline.
7. Never treat successful local unit tests as AWS, reboot, or systemd acceptance.

## Engineering evidence and remaining gates

A standalone Python installer candidate and adversarial tests were exercised
locally in an isolated filesystem harness: **26 tests passed**. Scenarios
include safe/unsafe service and timer templates, explicit approval, existing
symlink refusal, clean install/rollback, journal reuse, tampered unit refusal,
partial installation failure, active timer refusal, mismatched shared SHA,
corrupt/world-readable journal, bounded subprocess output and timeout, and
sanitized child environment.

At this checkpoint the installer candidate was **not yet integrated or CI-tested
in this repository**. Do not deploy that standalone candidate. Before approval,
complete code review, exact-head CI on
PostgreSQL 16 and 17, Linux/systemd staging with real unit states, authenticated
database and PAPER-isolation audits, private snapshot verification, and a
separately approved reboot/rollback acceptance.

The canonical dashboard activation guard remains unchanged and BLOCKED until
its own requirements pass. PAPER-only, LIVE disabled, and production database
preservation remain mandatory.

## Integrated read-only release-identity gate — 2026-10-20

The repository preflight now requires `--expected-shared` with the independently
reviewed 40-character release SHA and must run as root. It pins that exact
root-owned release below `/opt/hope-operations/releases`, requires `current` to
resolve to the same directory inode, authenticates the exact `deployed.env`
identity, and reads both exporter templates through no-follow directory-relative
descriptors. Template content hashes, type, owner, link count, permissions,
size and stable identity are checked before the result may reach
`REVIEW_REQUIRED`.

The credentials file is never read or printed. Its descriptor metadata must
identify one root-owned regular inode with exactly one link and no group/other
permissions; symlinks, FIFOs, hard links and permissive modes fail closed.

This closes a demonstrated diagnostic gap: a `current` symlink to a directory
outside the release store, containing arbitrary lookalike templates, previously
produced `REVIEW_REQUIRED` with no findings. The preflight remains diagnostic;
it still reports database identity and PAPER isolation as `NOT_VERIFIED`, always
returns a non-success exit status, and never installs or changes anything.


## Strict exporter absence metadata — 2026-10-20

Exporter absence requires all five properties to agree: LoadState=not-found,
ActiveState=inactive, and empty UnitFileState, FragmentPath and DropInPaths.
A known systemctl missing-unit status (1 or 4) is accepted only with that complete
unique UTF-8 record. Unknown exit statuses, contradictory states, missing/duplicate
properties and oversized responses fail closed. A zero exit status cannot make a
nonempty or unknown unit-file state count as absence.

This applies only to diagnostic inspection of the two exporter units. It does
not change PAPER absence handling or authorize an installer. The preflight still
returns exit 2 even for REVIEW_REQUIRED, never changes host state, and reports
PAPER isolation and database identity as NOT_VERIFIED. Operator review, release
identity, credential metadata and separate activation gates remain required.

Regression checks reproduced two false rejections for canonical missing-unit
responses and three failures to flag contradictory unit-file states before the
fix. Native filesystem/systemd/AWS acceptance is not inferred from portable API
doubles; full exact-head Linux CI remains required.

## Bounded native systemd observation — 2026-10-20

The earlier preflight asked `subprocess.run(capture_output=True)` to retain the
entire systemctl response and checked the 64 KiB ceiling only after the child
exited. A pathological property response could therefore consume unbounded
memory or leave descendants holding the output pipe until the timeout.

The inspector now reads stdout incrementally to an absolute byte ceiling and
wall-clock deadline, discards stderr, uses a fixed minimal environment, starts a
separate process session, and kills/reaps the complete child process group on
timeout or overflow. Native adversarial tests cover oversize output followed by
a hung child, a descendant retaining the session, discarded high-volume stderr,
exact successful output and invalid bounds.

Diagnostic evidence is also separated: `release_identity` records only the
reviewed release proof, while `exporter_absence` records the two canonical unit
observations. A later configuration failure no longer rewrites successfully
verified release evidence, but any finding still keeps the overall result
`BLOCKED`. This remains read-only and non-authorizing.
