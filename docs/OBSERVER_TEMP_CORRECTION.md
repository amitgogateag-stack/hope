# Observer TEMP correction: review-only, database-scoped and reversible

This batch prepares an exact PostgreSQL permission proposal. It creates no LOGIN,
issuer, approval, service or financial authority. There is no apply endpoint or
automatic privileged connection. Generated SQL requires explicit operator approval.
PR118 remains the independent, unchanged observer implementation base.

## Impact and smallest correction

`capture_temp_boundary` uses a fresh, bounded READ ONLY transaction. It captures
cluster/database OID/name/owner, normalized database ACL, every current role's
identity/attributes/effective CONNECT/CREATE/TEMP privileges, actual inherited
and conservative MEMBER closures, and membership options. No passwords, SQL
queries from other sessions or application rows are read. An inventory exceeding
256 roles is refused rather than silently truncated.

`propose_temp_restriction` requires explicit application-role dependencies.
It refuses an unreviewed LOGIN that would lose TEMP. Existing application access
is preserved by granting TEMP directly only where its prior effective TEMP relied
on PUBLIC; owners/superusers/existing direct grants require no extra grant. A
preserved dependency reachable by the observer is refused even with INHERIT FALSE.
No role membership is removed or changed to make a proposal pass.

The proposed change is scoped to one exact database: explicit needed TEMP grants,
then REVOKE TEMPORARY from PUBLIC. CONNECT, CREATE, table/column/sequence rights,
other database ACLs, financial policy and all existing data remain unchanged.
Non-login roles and future logins lose the PUBLIC default: every such role impact
is reported for review, not silently declared irrelevant. A source search or idle
session inventory cannot prove every future/external application's requirements.
An operator must review external SET ROLE/temporary-table dependencies before
approving. Required known application identities must remain in the dependency list.

PUBLIC TEMP grant chains, grant options or unsupported grantors are refused.
This deliberately supports the common single-owner PUBLIC TEMP grant only.
Removing a direct observer grant alone cannot override PUBLIC inheritance.

## Exact guards and rollback

The immutable proposal retains normalized before/after JSON and SHA256 values.
Generated SQL requires a fresh session logged in as the exact database owner;
SET ROLE/admin impersonation is not a fallback. It checks the complete live
boundary, executes all changes in one bounded transaction, and rechecks the exact
predicted boundary. Stale ACLs, login replacement/attribute drift, changed
memberships, wrong database/cluster, replay and unexpected postconditions abort.
A failed postcondition rolls back all earlier changes. Object identifiers are
strictly validated; catalog text cannot escape SQL dollar quoting.

The rollback restores PUBLIC TEMP and removes only new direct TEMP grants added
by this proposal. It requires the exact predicted after-state; concurrent external
changes are preserved by refusing rollback, not overwritten. The postcondition
must equal the original normalized ACL and effective-role state. ACL array order
or NULL/default storage representation is not a claim of byte identity.

These pre/post checks are not an independent cryptographic admission service or
financial COMMIT/revocation protocol. The DBA must serialize operator activity and
inspect the actual result/COMMIT acknowledgment. No automatic retry follows an
uncertain outcome; re-inventory first. Do not run the script inside an existing
application/financial transaction. Use the reviewed file with psql -X and
ON_ERROR_STOP=1 only after approval, from the named owner session.

## Preparation and evidence

Prepare using a separately authenticated READ ONLY connection, never a privileged
fallback. Example API shape (identities and dependency names must come from actual
private inventory, never examples or fixture names):

    with observer_engine.connect() as connection:
        before = capture_temp_boundary(connection, reviewed_observer_name)
    proposal = propose_temp_restriction(before, application_roles=reviewed_dependencies)
    apply_sql = proposal.sql()
    rollback_sql = proposal.sql(rollback=True)

Store both SQL files, before/after JSON, normalized hashes, dependency impact and
capture timestamps privately with mode0600. No SQL is executed by this API.
Native tests explicitly create disposable owner/reader/application LOGINs; they
exercise real TEMP table creation/denial, retained application rights, exact
rollback, stale/replayed plans, wrong actors, membership drift and partial-change
rollback. These fixtures do not prove AWS acceptance or authorize production grants.

## One consolidated host approval boundary

The private deployment packet must include exact source/CI/tree/archive hashes,
actual catalog evidence and known service/role dependencies, these guarded SQL
files and their inverse, reviewed runtime/build manifests and trust evidence,
PAPER-isolation/absence review, exact private reader/publisher unit configuration,
backup hashes and restart/publication/rollback verification commands.

Existing source/runtime absence must be reported as missing evidence. Do not
manufacture approval signatures, rewrite old release/template hashes, weaken
PAPER-absence classification or enable a service to obtain a passing result.
If actual prerequisites remain missing, the consolidated request must state which
parts can run and where execution must stop. Approval does not waive a failed gate.

After explicit approval and qualified prerequisites, verify at least three genuine
minute-boundary DB timestamps and bound receipts in Chrome. Browser polling alone
is not a successful export. Until then automatic AWS refresh remains unqualified.
Independent production admission A1 remains open; operational PAPER and LIVE stay
disabled. Financial strategy/risk logic, historical migrations and frozen refs are
unchanged. Genuine market-data work can proceed independently on a sibling branch.
