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

Reviewers must verify both PG16 and PG17 full migrations and integration
regressions; the new migration is unverified until those CI results exist.
Do not interpret an unrelated green unit test as migration proof.

No LIVE or PAPER activation, production issuer, AWS, broker, deployment,
risk change or frozen-baseline mutation is authorized. A1 remains OPEN and
operational PAPER remains NO-GO.
