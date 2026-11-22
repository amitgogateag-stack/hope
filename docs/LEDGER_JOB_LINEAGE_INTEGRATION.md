# Read-only ledger job attribution integration

This follow-up explicitly depends on unmerged PR109
d1553a64f915a31027fc175e7408403d83937d27 and preserves it unchanged.
Its source1981/run43832033451 and PR1982/run43832044141 passed5745 tests
per PG16/17 job, zero failures/skips,91.33%coverage; independent123 remains red.

The next fetch found PR107 advanced to87a49ebf4b7e590e8386fbfcbf4737d3604ea1de.
That separately authored change was reviewed before integration. Exact source1976/
run43827758653 and PR1977/run43827766013 passed5698 tests per PG16/17 job,
91.32%coverage. Its independent121/run43827766018 remains red. Those parent
runs do not qualify this new combined tree. Neither PR is merged or rewritten.

## Contracts retained together

A matching FILL entity ID alone is insufficient for the dashboard's attributed
receipt count. The effect must join an existing job record in the lowercase
`paper:` namespace. Missing/outside-namespace jobs are explicitly unattributed.
This is structural aggregate association, not independent signature verification,
full financial-lineage authentication, current eligibility or execution authority.

Both layers are retained: the new unattributed count/state and legacy snapshot
fallback, plus109's canonical-subset and application/accounting-bijection checks,
typed validators, bounded publisher and genuine LOGIN tests. Browser/server
validation and state precedence agree. Existing snapshots lacking the new field
hide the execution-ledger section rather than inventing a zero. No API size limit
is relaxed. Two redundant standalone JavaScript comments are removed so all UI
warnings, checks and new labels fit the unchanged65536-byte preview boundary.

Two merge conflicts were resolved explicitly in preview and browser tests. Both
sets of adversarial assertions survive; the same parent field is added to the109
ledger fixtures and before/after LAB readback. Three changed dashboard modules
now receive pinned static type qualification rather than only the original two.

## New real PostgreSQL evidence

Fourteen cases use fresh private UUID schemas with deliberately unconstrained
legacy-like tables. These are synthetic corruption fixtures, not broker fills,
operating trading evidence, production approvals or strategy qualification.

Eight cases exercise missing/wrong/case-mismatched job namespaces with one or
three duplicate receipt rows. They remain unattributed, never applied/reconciled.
Four cases exercise India/USA PAPER-prefix jobs with duplicate legacy receipts;
distinct-fill counts remain one and missing application remains explicit.
Two cases attach both valid and conflicting attribution to the same fill. The
strict projection rejects that impossible set relationship with the specific
LEDGER_COUNT_INCONSISTENT error, preventing a verified new snapshot. The publisher's
existing last-good/FAILED-receipt behavior applies; the reader never repairs data.

The first local expectation incorrectly assumed contradictory counts would return
one UNAVAILABLE section. Actual behavior rejects the entire invalid projection.
The regression now requires that exact stronger refusal; no catch-all success,
production relaxation or generic-error assertion is introduced. Every normal
observation runs twice in an actual READ ONLY, REPEATABLE READ transaction, checks
read-only mode and unchanged receipt-row counts. No query writes financial state.

## Release and remote-data limits

The reported remote pair of unapplied LAB fills is still unresolved. This field
helps distinguish structural attribution once genuine read-only data is available;
it does not identify those rows. The latest AWS attempt was refused by an expired
login before submission. No fresh remote SHA/service claim, credential change,
deployment, migration, grant, service change or operational cycle is made here.

All independent production-attestor/session/XID/COMMIT-policy/revocation blockers
remain. No signer/root/approval is fabricated, no worker document becomes remote
authority, and no human review is claimed. PAPER-only, LIVE disabled, production
and engineering integration NO-GO. Domain logic, risk thresholds, strategies,
financial writers, all240 migrations, frozen refs and original PRs are preserved.
No G7FX or real orders. Final exact source and PR PG16/17 results must be recorded
in the follow-up draft. If rejected, leave it unmerged; tested PR109 remains intact.
