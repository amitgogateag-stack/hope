# R1 remediation: capital provisioning must not share startup authority

Independent reviewer reproduced a certified startup cycle which committed an
unrelated `paper_portfolios` row with `initial_cash=cash=123456789` while
the cycle reported `NO_TRADE`. This is a financial write-set violation under
the explicit disposable owner-model approval fixture, not a production issuer
bypass.

Migration 238 adds a native PostgreSQL row trigger that rejects **INSERT**
into `paper_portfolios` for a `CONTROLLED` startup principal. It also
rejects changes to `initial_cash` by that principal. Existing
`hope_admission_mode()` classification is reused, so unknown and
unapproved principals continue to fail closed under migration 237. The
trigger is intentionally independent of the worker's claimed cycle/job.

The regression injects an unrelated portfolio during the certified cycle's
commit-phase callback. It requires the explicit denial and zero committed
unrelated portfolios, financial effects and startup financial transactions.
It uses the existing disposable restricted-worker integration fixture.

## Boundaries

This is a **narrow first R1 guard**, not complete financial write-set
authorization. It does not yet restrict all financial rows to a signed
portfolio/instrument/quantity/effect manifest. It does not remove ordinary
portfolio-provisioning privileges or introduce a separate funding authority.
Those remain required before operational admission.

Migration 239 additionally rejects every directly or indirectly reachable
PostgreSQL predefined `pg_*` role at the controlled worker preflight. Powers
such as `pg_execute_server_program` do not appear in table/schema ACL checks but
cross the database/host boundary and are incompatible with a restricted PAPER
financial principal. The check runs at bootstrap and, inside PostgreSQL, before
every controlled one-use startup/execution authority insert. Privilege drift
after a safe bootstrap therefore cannot consume an execution claim before the
financial-write boundary notices the escalation.

Direct grants on privileged `pg_catalog` routines are rejected too. PostgreSQL
allows an administrator to grant a superuser-only routine such as
`pg_read_file(text)` without granting its equivalent predefined role. Migration
239 rejects catalog EXECUTE capabilities that PUBLIC does not possess, while
retaining the explicitly required read-only `pg_control_system()` identity
probe. The regression demonstrates the restricted worker can read only the
disposable test server's `PG_VERSION`, then requires refusal before an execution
claim is consumed and revokes the test grant.

The same recheck rejects ownership of any public relation by the login or a
reachable role. Relation ownership is an implicit DDL capability: an owner can
disable the financial triggers even when ordinary CREATE and DML grants look
restricted. The adversarial ownership-transfer regression restores the exact
prior owner after its disposable proof.

Public routine ownership is rejected for the same reason. Owning a trigger
function is destructive DDL authority over a guard even without relation
ownership. The routine regression transfers the financial-write trigger
function after bootstrap, requires execution refusal, and restores its exact
prior owner.

Reviewers must verify both PG16 and PG17 full migrations and integration
regressions; the new migration is unverified until those CI results exist.
Do not interpret an unrelated green unit test as migration proof.

No LIVE or PAPER activation, production issuer, AWS, broker, deployment,
risk change or frozen-baseline mutation is authorized. A1 remains OPEN and
operational PAPER remains NO-GO.
