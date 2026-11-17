# R1/R2 qualification batch — November 15, 2026

Target: December 1 engineering qualification. This draft is not release approval.

## Dependency decision

Base: PR103 b821f19b37c2083a0b2b054ddf1fb7c3902d31de. It preserves the
R1 transaction-owned write-set/COMMIT guards from 95/96/97, selected compatible
R2 snapshot/admission safeguards from 99/100 via101, acceptance containment102,
and non-trading staging103. Earlier83–90 and93/94 safeguards remain ancestors.
Alternative98 overlaps96/97 and is deliberately not blindly merged.

PR104 38bfb3e21a9f927d4a64eb9c4953c8e3cfc19c45 has successful source/PR
HOPE CI runs43706569907/43706573154, but its independent acceptance run
43706573094 fails. Its stack predates substantial R1 work in103. Replacing103
with104 would discard safeguards. This batch instead ports91's reopened OCI
manifest digest binding and104's pinned runtime read/error contract, preserving
current release_source, type, runtime and write-set checks. Existing provider
bar detachment in103 is stricter than the older84 change and is retained.
No protected branch or existing PR is merged or rewritten.

## Reproduced and repaired

Before the OCI fix, the added substitution test returned VERIFIED after the
manifest was replaced between image verification and parsing. It failed as
expected. Parsing now consumes bytes from the bounded descriptor reader
authenticated against the previously verified raw digest. Runtime pinned
manifest reads use the same primitive. Only its exact digest mismatch maps to
RUNTIME_MANIFEST_CHANGED; unrelated read errors retain their identity.

## Publisher provenance: containment, not completed production remediation

The existing production observer continues to deny unsigned heartbeat
authentication, even with a live lease. No laboratory key or status can satisfy
production publisher, controller, database-session or PAPER admission gates.

The new verification-only lab channel uses Ed25519 over canonical bounded
documents, exact release/session/publisher/key context, a random observer
challenge, contiguous sequence watermark, generation/expiry (maximum15seconds),
and irreversible observer revocation. Identical polls do not refresh expiry.
Observer restart generates a new challenge; publisher restart requires explicit
new context. Same-UID file replacement, forged freshness/identity, replay,
restart and concurrent polling are tested using real synthetic signatures.

This assumes the configured laboratory verifier/key input is trusted relative
to a file-only attacker. It does not prove private-key custody against arbitrary
same-UID process memory access, nor independently authenticate another process's
kernel/database session. Production HIGH remains open: independently enforced
signer custody, authenticated observer/controller session binding, and reviewed
commit-coordinated policy/revocation protocol are still required. A self-process
Permit, file ownership, PID, lease or this lab verifier is insufficient.

## Isolated lifecycle and dashboard

Existing certified simulated PAPER tests now run both India and USA sessions for
missing commit-reconciliation receipt recovery, pre-COMMIT backend death,
post-COMMIT authentication interruption, future-effective/stale/missing quotes.
All original accounting, once-only strategy, receipt, rollback, risk and terminal
restart assertions remain unchanged. Existing dual-market NO_TRADE/FILLED/
RISK_BLOCKED paths remain in the suite. Test-owner fixture authority is synthetic;
none is production admission. Broader partial/rejected/reconciliation tests remain
in the inherited suite; this delta does not invent new strategy behavior.

The read-only /paper-lab.json endpoint defaults UNAVAILABLE. Optional programmatic
configuration is for disposable harnesses only; no command-line production
enablement is added. It accepts only the dedicated verifier class and bounded
signed packets, conceals input errors, and always reports production BLOCKED and
execution_authorized=false. The Safety panel renders lab cases as text, including
FAIL/NOT_EXECUTED, and labels last_lab_cycle_at separately from any operational
cycle. A signature authenticates the configured lab producer's report, not its
truth, database attestation, strategy qualification or actual production state.

## Safety and remaining acceptance

No migrations, grants, credentials, financial rules, strategies, risk thresholds,
AWS deployment, operational PAPER cycles, broker orders or G7FX work. Frozen
p0-pit-context860dbba7 and day29-green-baseline7e9d3417 remain untouched.
Native POSIX descriptor tests run in Linux CI. Windows tests model only the
file-read boundary where lstat/fstat creation/change timestamps differ; real
signature and digest verification are not mocked.

Engineering integration and operational PAPER remain NO-GO pending independent
human security review and genuine production trust/provider/session/restore
evidence. CI and disposable simulation never establish AWS readiness.
