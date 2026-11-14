# Independent acceptance provisioning boundary

No production authority is provisioned by this repository. Engineering CI,
signed build provenance, local completion and file integrity are distinct from
independent operational acceptance. Operational PAPER remains NO-GO.

## Report-generation repair

`release_acceptance.acceptance_observation` previously returned VERIFIED when
caller-supplied labels said COMPATIBLE/CURRENT/CLEAR and a runtime summary had
the expected shape. No signature, independent observer or replay check
authenticated those labels. It now always retains BLOCKED and
REVIEW_INDEPENDENT_ACCEPTANCE_AUTHORITY, alongside all existing fault blockers.
There is no caller boolean, injected positive verifier or configuration switch
to remove that blocker. The bounded acceptance projection preserves it.

`deployment_rehearsal.rehearse_upgrade` still exercises the existing disposable
activation/rollback state machine. Successful completion now writes
lab_result=COMPLETED, state=UNVERIFIED and independent_acceptance=UNVERIFIED.
It does not issue a VERIFIED acceptance report. Runtime provenance and original
crash/recovery assertions remain mandatory; the activation implementation is
unchanged. A digest authenticating the resulting bytes cannot turn that
UNVERIFIED report into positive acceptance.

The regression-first tests failed on the PR101 base: both producers emitted
VERIFIED without acceptance authority. Tests model local completion only; they
do not manufacture an independent producer or claim native activation locally.
The existing native signed-runtime rehearsal covers actual journal/rollback
behavior in PostgreSQL 16/17 repository CI.

## Required operator provisions and review

These are requirements for a separately reviewed protocol, not installable
instructions, existing APIs or permission to change any production resource.

| Authority | Accountable operator role | Required independently supplied evidence |
|---|---|---|
| Trust anchors and custody | Security/signing owner | Reviewed signer identities, scoped public trust anchors, key provenance, rotation/revocation and proof the worker, controller, observer and build process cannot obtain signing capability. No fixture keys or self-generated approval rows. |
| Controller/database binding | Runtime security owner and DBA | Independently observed kernel incarnation, approved image/runtime/namespaces/UID, actual PostgreSQL backend incarnation, login OID/name, database identity and transaction XID bound together. PID, argv, worker Permit, GUC or a self-process check alone is insufficient. |
| Financial authorization | Financial control owner | Independently authorized initial-capital source and exact transaction-owned expected financial writes for the approved cycle. Separate capital provisioning from controlled fills; include before/after values, relation identity and complete write set. Worker-observed actual writes are not their own expected authority. |
| Publisher authority | Runtime security/signing owner | Authenticated exact heartbeat/snapshot content bound to release, producer incarnation, audience and session; one-use challenge/sequence and bounded validity. Enforced writer isolation or reviewed independent signing custody must survive same-UID replacement and restart. A file lock or same-file digest/sidecar is insufficient. |
| Policy and COMMIT coordination | Security owner and DBA | Reviewed ordering of policy generation, HALT, revocation, transaction locks and actual deferred COMMIT. Expiry/revocation cannot be checked only before dispatch; unknown COMMIT stays consumed and cannot trigger automatic replay or HALT clearance. |
| Independent acceptance | Independent reviewer/operator | Authenticated observations from the approved identities above, pinned to exact release/tree/runtime/schema and authorized session/cycle. Freshness, nonce/sequence replay protection, revocation and observer separation must be verified together; a signed arbitrary verdict is insufficient. |

All twenty production acceptance scenarios in the independent-security harness
remain FAIL/unexecuted until a genuine positive admission authority exists.
Its passing denial probes and the separate disposable session/write-set
prototypes do not execute those independent acceptance scenarios.

## Release and fallback sequence

1. Preserve the validated PR101 integration and its R1/R2 ancestry. This bounded
   report repair builds on its exact ae6081c base; it does not re-merge overlapping
   PR98 or modify authoritative/frozen refs.
2. Obtain human review of the combined diff and the authority/custody/session/
   publisher protocol above. Implementing-agent/static review is not approval.
3. By the November 17 fallback, provision and independently authenticate the
   missing roots in a separately approved environment, then execute the twenty
   acceptance scenarios, revocation/expiry/restart/ambiguous-COMMIT attacks and
   restore/provider/calendar/factory verification. No deadline waives a gate.
4. If provisions are missing, deliver the exact validated fail-closed engineering
   candidate and this specification. Keep operational PAPER NO-GO; do not merge,
   deploy, activate, change production grants/credentials or execute a PAPER cycle
   without the required separate approval.

PAPER-only India/USA; LIVE disabled. NO_TRADE/HALTED remain valid. Strategy and
risk thresholds are unchanged. G7FX remains NOT STARTED/deferred issue92.
