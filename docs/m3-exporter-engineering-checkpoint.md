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

This candidate is **not yet integrated or CI-tested in this repository**.
Do not deploy it. Before approval, complete code review, exact-head CI on
PostgreSQL 16 and 17, Linux/systemd staging with real unit states, authenticated
database and PAPER-isolation audits, private snapshot verification, and a
separately approved reboot/rollback acceptance.

The canonical dashboard activation guard remains unchanged and BLOCKED until
its own requirements pass. PAPER-only, LIVE disabled, and production database
preservation remain mandatory.
