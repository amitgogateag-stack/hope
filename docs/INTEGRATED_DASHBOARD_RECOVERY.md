# Integrated dashboard recovery and lifecycle evidence

This candidate preserves both validated histories: PR107 at
237c26f94385dc724b436b8cb97299448417b738 and PR108 at
7605e0aaaaea8ed963fc9d749edb4690520aa731. Neither PR is merged. The target
integration branch p0-pit-context remains 860dbba7; frozen day29 remains 7e9d3417.
Parent107 source1974/43820589343 and PR1975/43820596020 succeeded: four native
PG16/17 jobs5696 passed each. Earlier10336de run43813457398 also succeeded;
its independent43813457377 failed at absent independent positive attestation.
Parent108 source1960/PR1961 passed5674 each. These are parent evidence, not
qualification of a newer combined head. Final exact-head evidence belongs in the PR.

## Reproduced and fixed in this batch

Four native regression cases failed before the publication fix: snapshot/status
same path, status replacing the held persistent lock, hardlinked output/status,
and symlinked receipt. Collisions could destroy last-good bytes or replace the
locked inode and permit split-lock publication. The refresher now requires three
distinct artifacts in one private publication directory, rejects unsafe existing
snapshot/receipt metadata before DB access, and rechecks under the held lock and
before publication. Existing unsafe-lock refusal behavior remains unchanged.
Staged input uses the bounded no-follow stable-inode reader rather than read_bytes.
Oversized, linked and substituted stages preserve last-good output and emit a closed
FAILED receipt. Native tests actually SIGKILL a child publisher during export,
after snapshot replacement and after receipt replacement. A torn pair cannot
claim CURRENT; actual process death releases the persistent kernel lock and a
fresh publisher can recover. A hostile process sharing the publisher UID is still
outside this local file-integrity trust boundary, not independently authenticated.

Six ledger characterization cases failed before the aggregate validation fix.
In particular, two canonical fills, zero applications and zero reported canonical
unapplied fills could be labeled OBSERVED_COMPLETE. Validation now requires the
canonical applied subset to fit within actual applied fills. Unique portfolio/fill
accounting receipts must exactly match applications with accounting, after orphan
receipts are removed. Server and browser enforce both identities. One older positive
fixture had two applications, zero receipts and only one missing receipt; corrected
its synthetic missing count to two, retaining the expected missing-accounting state.
These observations do not authenticate financial effects or authorize replay.

Both-market persisted FILLED/NO_TRADE/RISK_BLOCKED tests now read the actual ledger
inside READ ONLY, REPEATABLE READ transactions before/after their committed case.
They require exact fill/application/accounting/canonical-receipt deltas and no
increase or invented clearance of unrelated historical backlog. Existing real
signatures, immutable hashes, twice-idempotent reconciliation, restart/no-rerun,
HTTP isolation and no-extra-effects assertions remain unchanged.

## The reported two unapplied fills

The actual remote pair is **UNRESOLVED**, not established to be fixtures or a
processing defect. Fresh read-only AWS verification was attempted this sprint;
the CLI refused with an expired login before any command was submitted. No new
deployed SHA or service assertion is made from that attempt. Previous dated AWS
metadata remains historical. No credentials were changed and AWS was not modified.

The numbers alone cannot distinguish legacy/untracked fills, canonical effects
without portfolio applications, missing accounting, or corrupt identities. The
parent dashboard exposes these cases separately. Local real-DB partial receipt
tests and certified lifecycle tests exercise those distinctions; they do not
identify the remote rows. Once separately restored read-only access is available,
use one READ ONLY, REPEATABLE READ transaction with bounded statement/lock timeouts
on the *identified disposable database*, never an assumed production connection.
Trace each reported fill through order/signal/instrument identity, canonical FILL
effect/job, application keyed by portfolio/fill, position, PNL receipt, job completion
and reconciliation audit. Compare counts to the same exported snapshot timestamp.
Do not insert application rows, replay jobs, clear HALT, reset fixtures, or infer
success from an aggregate count. Preserve private identifiers in private evidence.

## Independent admission and release boundary

Standalone43813457377 really executed12 containment attacks per PG16/17, all denied.
All20 mandatory positive production scenarios remain FAIL / proof not executed.
The standalone runner has no positive issuer: three genuine separate LOGINs model
containment, the verifier cannot approve, and production_verification=false.
Green R1 write-set/session prototypes cannot replace an independently held signer,
authenticated kernel/controller incarnation-to-database-session/XID binding, or a
reviewed protocol serializing current policy/revocation with actual COMMIT. A
worker-supplied Permit, local file digest, owner approval fixture or implementing
agent review does not provide that trust root. Admission stays blocked.

No new key, issuer, approval/grant, production migration, broker connection,
activation, strategy change, risk-threshold change, or G7FX work is introduced.
Production and engineering integration remain NO-GO pending genuine independent
attestation, external provider/calendar/factory/runtime/observer/restore evidence
and independent human security review. This is an unmerged laboratory candidate.

## Rollback and verification

No schema migration is added. If rejected, leave the new draft branch unmerged;
original107/108 heads and frozen references remain available. The stricter refresher
requires snapshot and status in the same private directory; previously accepted
conflicting configurations are errors before export. Existing valid configurations
and non-authorizing last-good display are retained. Full native source and PR CI
must pass at the final candidate SHA; independent security stays red until genuine
positive production proof exists. Never count an infrastructure refusal as a pass.

## Targeted static type qualification

Targeted pinned mypy1.19.1 review found14 pre-existing heterogeneous-dictionary/
fixed-tuple inference diagnostics on parent107 and17 on the initial candidate
(three new arithmetic expressions inherited that incorrect str inference).
Explicit count/timestamp types, the heterogeneous JSON result dictionary and
variable-length string tuple annotations resolve all17 without type ignores,
runtime validation changes or strategy/risk changes. Both changed modules pass
--check-untyped-defs; an early pinned CI gate now maintains that qualification.
This targets the refresher and projection modules, not whole-program typing.
