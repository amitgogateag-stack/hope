# Independent admission verifier: fail-closed implementation

Status: HIGH A1 OPEN, existing containment preserved. Engineering integration NO-GO;
operational PAPER NO-GO. This is an incomplete implementation, not an admission verifier
capable of issuing approvals. Related candidates: #85 and #86 remain draft/unmerged.
Base: 7a97efdbb871b362f26456e882d5c2eeb53395f3. Frozen baseline
7e9d341742818a97accce3e2301b1c7f96596795 is not modified.

## Threat model and missing trust capability

The worker may control its arguments, SQL, Permit objects, signature-result flags,
claimed PID, database backend PID, namespace hashes and runtime metadata. It may
reconnect, fork, replace a process, race mutable policy or borrow another session's
approval. Database owner/administrator, kernel, deployment operator and independently
provisioned authority roots are trust boundaries. Owner compromise is out of scope;
owner-written rows are NOT evidence of independent worker authentication.

There is no configured trusted kernel/runtime observer that cryptographically binds
an authenticated target controller to its actual PostgreSQL client connection and
transaction. SO_PEERCRED alone authenticates a local socket peer, not that peer's
separate PostgreSQL session; pg_stat_activity PID and client addresses alone are not
such a binding. Existing self-observation routines cannot establish it from a separate
process. Accepting signed worker claims would conceal this gap. Neither a trusted
session-terminating proxy nor a reviewed server-side attestation extension is present.
The current Windows workspace has no discovered Docker or PostgreSQL executable.
Linux CI can test denial, but has no configured production session attestor either.

## Implemented executable boundary

`python -m hope.security.admission_verifier` accepts one bounded JSON object on stdin,
returns DENY and exit 78, and never connects to a database. There is no configurable
allow switch, worker-selected provider, synthetic identity fallback, approval INSERT,
or production credential. Its protocol identifier is versioned; no challenge-response
protocol is falsely advertised as implemented. Invalid/oversized inputs also deny.
The unit suite launches the executable as a subprocess in addition to direct checks.
Separate runtime OS identity and authenticated IPC are deployment prerequisites,
NOT implemented by launching a subprocess in CI.

## Required protocol before issuance can be implemented

A reviewed deployment must first supply a verifier-controlled session proxy or server
extension that binds the TLS/client connection, database cluster identity, backend
start identity, actual transaction xid8, login OID/name and controller kernel identity.
A PID, application_name, user-set GUC or worker-generated digest cannot be that proof.
A verifier-created random one-use challenge should bind a canonical domain-separated
transcript containing all those identities plus release commit, content-addressed
runtime image, executable, namespaces, process start time/pidfd, authorization digest,
transaction/cycle, authority issuer role/scope/key, policy generation and deadline.
The attestor response must be authenticated by a root unavailable to the worker and
must attest observations, not sign worker-supplied assertions. Independently verify
Ed25519 authority bytes, pinned roots, issuer role, exact scope, expiry and current
revocation before issuing anything. A session-bound channel and single-use database
consumption must prevent borrowing; migration 237 approvals currently do not provide
that session binding. Do not enable issuance using the proposed transcript alone.

## Database privileges and COMMIT limitations

Disposable CI role matrix:

| Role | LOGIN | Own schema/DDL | Approval writes | Financial INSERT |
|---|---|---|---|---|
| owner | yes | yes | existing owner boundary | trusted test migration only |
| verifier | yes | no | no (issuance disabled) | no |
| worker | yes | no | broad accidental INSERT tested, trigger denies | broad INSERT tested, admission denies |

All three roles are NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
NOINHERIT, with random credentials and no role memberships. PostgreSQL service admin
only provisions disposable roles; the worker never uses that connection. Public schema
CREATE and database TEMPORARY are revoked. Owner migrates; workers cannot self-classify,
become superuser, or SET ROLE to verifier. No production roles/grants are installed.
Migrations 001-237 are retained byte-for-byte; no new migration is appropriate while
there is no authenticated issuer or deployable COMMIT coordination protocol.

Existing migrations 234-237 enforce their documented transaction/deferred constraints,
expiry and owner-protected exact-row checks. This change adds no new HALT/revocation
atomicity guarantee. A future design needs a common database serialization fence for
policy generation, revocation and HALT mutations and every admitted financial transaction,
with row locks held through COMMIT and mandatory writer interfaces. Specify whether a
mutation takes effect before a transaction or waits behind it; do not promise that a
revocation cancels an already serialized COMMIT. Recheck current principal, transaction,
cycle, consumption, deadline and policy at admission and financial commit. External
signing-authority state and kernel observations cannot be atomically committed with
PostgreSQL; pin bounded attestation leases, define observation/serialization instants
and refuse stale evidence. Deadline checks do not guarantee client acknowledgment or
WAL durability before an absolute wall-clock instant. Unknown commit outcome must be
reconciled from durable witnesses, never retried as a fresh authorization.

## Independent evidence and CI

`security/run_adversarial.py` is a standalone runner, outside pytest discovery and without
imports from tests/admission_fixture.py. It applies the complete canonical migration
inventory and re-applies it to verify recorded checksums. It records version/checksums,
SQLSTATE denial evidence and zero rows in financial/admission tables after attacks.
It requires PostgreSQL 16/17 and refuses a populated public schema. Run only on disposable
infrastructure: it deliberately leaves randomly named roles to the service's destruction.
It never writes connection secrets into evidence.

The standalone runner now attempts twelve deny-only containment experiments:
worker self-classification, self-approval, superuser grant, verifier impersonation,
schema-owner impersonation, self-granted verifier membership, shadow attestor function
creation, direct cash insertion; separately, the restricted verifier attempts approval
issuance, portfolio funding, and self-classification; finally the missing-attestor
executable must deny. These checks require genuine PostgreSQL privilege/trigger
denials and leave the protected financial/admission tables empty. They do NOT
constitute the twenty required lifecycle/security proofs. All twenty are separately
inventoried as FAIL, including the genuine authorized PAPER cycle. The independent
negative-evidence validator now demands the exact 12-probe name/SQLSTATE set,
rejecting omitted, duplicated, unexpected or forged PASS records before setting
`containment_verified`. This label applies only to disposable denial checks and
never changes `production_verification: false` or the required A1 FAIL gate. No case is skipped,
xfail, or passed merely because a verifier is absent. The runner always exits nonzero
until implemented proofs can replace this incomplete gate. The JSON and Markdown reports
explicitly label this distinction. Infrastructure exceptions also fail and produce evidence.
The full existing CI and its coverage threshold remain unchanged.

`.github/workflows/m4-independent-security.yml` independently runs on PR and dispatch,
checks out the PR head SHA (not a mutable branch or merge ref), provisions fresh PostgreSQL
16 and 17 service instances, creates random role credentials, runs the standalone runner
and uploads machine/human reports. It has read-only repository permissions and no AWS,
production secrets or operational activation. It does not rely on existing CI or fixtures.
Same-PR workflow/runner code remains attacker-modifiable: require branch protection,
security CODEOWNERS review and a required trusted base-branch/external-repository workflow
that runs a pinned harness against the candidate SHA. This PR cannot make its own workflow
independently trusted or prevent a repository administrator removing required checks.

## Findings and delivery evidence

HIGH A1 remains OPEN: independent authenticated runtime-to-session binding, authenticated
issuer, commit-coordinated mutable policy and the 20 scenario proofs are incomplete.
MEDIUM: same-PR CI trust; resolve via trusted external/base workflow and branch protection.
No independent human review has occurred. Implementing-agent tests are not human approval.
Positive existing lifecycle tests model the external authority and cannot close A1.
See the final chat/PR and Actions artifacts for exact run counts; never infer PostgreSQL
success from a Windows local run or historical #86 CI. An intentionally red acceptance gate
is a correct record of missing proof, not a successful security certification.

Changed source inventory:
- src/hope/security/__init__.py
- src/hope/security/admission_verifier.py
- tests/unit/test_independent_admission_verifier.py
- security/requirements.json
- security/run_adversarial.py
- .github/workflows/m4-independent-security.yml
- docs/M4_INDEPENDENT_ADMISSION_SECURITY.md

Next action: commission and review the trusted kernel/controller-to-PostgreSQL-session
attestation interface before implementing any approval issuer or granting it privileges.
