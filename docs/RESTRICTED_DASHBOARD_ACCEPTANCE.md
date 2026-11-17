# Restricted dashboard acceptance and AWS/GitHub comparison

November 15, 2026. November17 target: isolated demonstration. December1 target:
engineering qualification. This change does not authorize operational PAPER.

## Reproduced setup gap

The existing missing-SELECT projection test created a temporary NOLOGIN role and
used SET ROLE from its setup connection. A genuine NOCREATEROLE database owner
failed with PostgreSQL42501 at CREATE ROLE, before any application assertion.
This was reproduced on real disposable PostgreSQL16 and17 at parent aa7ca10.
It matches the separately operator-reported AWS fixture failure; that report
alone was not treated as proof of application failure.

## Repair: real LOGIN, same security assertions

The test-only tests/dashboard_reader_fixture.py supports:

- A genuinely authorized disposable setup administrator creating a fresh,
  non-superuser/non-CREATEROLE LOGIN with a random ephemeral password. The test
  connects as that login directly; it never borrows the administrator's session
  through SET ROLE. It removes only its own freshly created role at completion.
- A NOCREATEROLE owner using an explicitly pre-provisioned, separately authenticated
  test-reader connection through HOPE_TEST_DASHBOARD_READER_URL. It does not
  create or elevate that role and does not delete a supplied role.

Both connections must identify the same host/port/database. The target must be a
fresh dashboard_m1_UUID test schema owned by the setup login. Reader current_user
and session_user must match the supplied login, and differ from the owner.
Superuser/CREATEROLE/CREATEDB/replication/BYPASSRLS, owner membership even with
NOINHERIT, schema CREATE, table ownership and effective scoped table writes are
refused. Existing direct schema-USAGE/table-SELECT grants are refused so cleanup
cannot silently revoke pre-existing rights. Intentional PUBLIC control-table
SELECT grants from the migrations remain intact. This is a fixture-scoped
permission proof, not qualification of an operational database principal.

Only the original selected test relations receive temporary SELECT, and only the
unique test schema receives USAGE. The datasets table remains forbidden. The
original assertion set is retained: datasets/strategy_catalog/certified_chain
UNAVAILABLE, strategies/decisions available, transaction_read_only=on. A direct
SELECT now also proves a real backend42501, independent of the projection's error
handling. No mocked database error, removed assertion, skip or owner fallback.

Cleanup revokes only the fixture's scoped grants. Different concurrent test
schemas use separate actual sessions. Real backend termination during cleanup
tests primary-error preservation and cleanup-only fail-closed behavior.

## Reproduce in an isolated disposable database only

Never point these variables at the operating hope database. No production grants,
roles, services, admission approvals or migrations are modified by this package.

For the full adversarial acceptance suite, provide a disposable administrator
with permission to create its fresh temporary databases and test roles. The suite
then creates an actual non-superuser, NOCREATEROLE database owner and separate
reader; it runs the original projection test through that restricted owner.

```powershell
$env:HOPE_DATABASE_URL='<disposable-admin-url>'
python -m pytest -q tests/unit/test_dashboard_reader_fixture.py tests/integration/test_dashboard_reader_boundary.py tests/integration/test_dashboard_m1_authenticated_projections_pg.py
```

For an already provisioned disposable NOCREATEROLE owner environment, ask its
authorized test administrator to provision a separate reader LOGIN with CONNECT
to that disposable database, no privileged attributes/owner membership and no
pre-existing rights on the newly generated test schemas. Supply its connection
privately; never paste passwords/DSNs into an issue or public log. Do not grant
CREATEROLE to the owner as a workaround.

```powershell
$env:HOPE_DATABASE_URL='<disposable-owner-url>'
$env:HOPE_TEST_DASHBOARD_READER_URL='<separate-disposable-reader-url>'
python -m pytest -q tests/integration/test_dashboard_m1_authenticated_projections_pg.py
```

Without a supplied reader and without setup authority, fail with
DASHBOARD_TEST_READER_SEPARATE_LOGIN_REQUIRED; do not silently skip or use owner
permissions. For the independent boundary suite, HOPE_TEST_DATABASE_ADMIN_URL
may separately identify the disposable administrator; it is never an application
or production admission credential.

CI runs the real-login/database-owner boundary early on both PG16/17, retains
every existing financial/startup/COMMIT/security test and the89% coverage floor,
then runs the complete suite. Local full-OS/native limitations remain explicit.

## Direct AWS comparison, no deployment

Existing AWS CLI authentication and SSM connectivity to the known HOPE instance
in ap-south-1 were verified. Bounded read-only command results:

| Observation | Actual result |
| --- | --- |
| Operations current | f17a54929a8ecb7745a62c118893b55494aeb2f1; tracked-clean |
| Legacy /opt/hope | 1f24358c70b7a9f4136ccf2fd0303d4d77605d9c; tracked-clean |
| PostgreSQL/database |16.15 /hope |
| Migration ledger |8 entries,001_initial.sql through008_migration_checksums.sql |
| Installed HOPE unit files listed |Two dashboard web services, both enabled/running |
| HOPE timers listed |None |
| Loopback8787 |GET /200; /status.json404; /healthz404 |
| Loopback8788 |GET /200; /status.json200; /healthz404 |
| Separate dashboard-current/staging-current pointers |Absent |

The old revision does not implement /healthz. Its404 is not evidence that the web
process failed, and HTTP200 is not trading/data-freshness/readiness proof. No
private HTTP payloads were copied. No claim is made about arbitrary processes
outside the inspected HOPE units or current provider/restore acceptance.

SSM evidence IDs: d24d45e0-8d79-4780-884a-22cef43f7236 (release inventory),
d47ab9de-b249-42ea-ac6e-aa40798d3c2c (units/READ ONLY schema metadata),
5958a93d-141b-4eb5-a80e-78a91e0c8513 (supported HTTP status codes). All completed
Success/response0. The previously quota-blocked metadata request was not executed;
the later request resumed only after the user confirmed credits were restored.

The current source inventory has240 migrations; AWS has8. Both AWS release SHAs
exist in GitHub history and are ancestors of the frozen integration. They are
older than the draft candidate. This is verified version/schema drift, not
permission to apply232 migrations or activate the draft. The isolated operator-
reported /var/tmp/hope-staging-nov17 checkout at aa7 is separate, not an agent-
verified operating deployment. This fixture patch has not been installed on AWS.

## Remaining release gate

Independent production signer custody, authenticated publisher/controller-to-DB
session/XID binding and COMMIT-coordinated policy/revocation remain HIGH blockers.
Independent security stays red; human review and genuine provider/calendar/
factory/runtime/observer/restore evidence remain required. Lab evidence never
supplies production authority. All production source, financial controls, risk,
strategy logic, historical migrations and frozen refs remain unchanged. No AWS
deployment, service restart, production DB mutation, real order, activation,
G7FX work or PR merge is performed.
