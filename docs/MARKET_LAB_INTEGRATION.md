# Market and LAB observation integration

This draft explicitly integrates qualified parent1797d8502a60e4508e1b6ec4582115810f67bf57 with preserved PR113212745e6ce72db4ed6961c515399c7e48bcc5654. Neither original PR is merged or rewritten. Parent179 sourceCI2013/run43990169124 and PRCI2014/run43990176480 passed5719 tests in each of four PostgreSQL16/17 jobs,91.31%coverage. PR113 source2011/PR2012 passed5822/job,91.27%. These are parent results, not final candidate qualification.

The integration brings bounded LAB case validation/expiry and canonical INDIA/USA/OTHER ledger observations into the consolidated candidate. Unknown exchanges or mismatched receipt markets remain explicit unresolved evidence; they do not authorize execution. Old snapshots missing required market evidence expose ledger UNAVAILABLE rather than inferred zeros. All financial, provider, scheduler, signing, COMMIT, strategy and risk implementation remains unchanged.

Regression-first review found five concrete evidence defects:

- All six ordered market pairs accepted an unapplied subset larger than that market's fill set while global totals remained valid. Server and browser now require each market's unapplied count not exceed its own fill count. Six valid partitions remain accepted, including explicit OTHER observations.
- Reordering valid JSON bucket keys changed the displayed I/U/O labels because rendering used Object.values. Rendering now names INDIA, USA and OTHER explicitly.
- Real disposable PostgreSQL corruption fixtures showed an inner instrument join hid persisted PAPER fills when canonical metadata was missing. A left join retains those known PAPER fills as OTHER/unresolved; no row is repaired or invented.
- A valid later LAB case reset an earlier invalid case's refusal. The browser now preserves any row failure; malformed first rows followed by valid rows remain unavailable.
- Future-dated LAB generation/completion could display as observed. Future generation is refused, while valid cases and independent expiry aging remain covered.

The existing 32 SQL lineage/status cases retain their assertions with explicitly modeled canonical instrument fixtures. Twelve additional real PostgreSQL cases cover matching/wrong market receipts, missing instruments and unknown/null exchanges. Repeated observations run REPEATABLE READ, READ ONLY and assert all six fixture-table row counts unchanged. Both-market FILLED/NO_TRADE/RISK_BLOCKED acceptance adds exact market bucket deltas without clearing unrelated historical backlog. The two historically reported remote fills are not identified or repaired by these synthetic tests.

Conflicts preserve the stronger existing three-stage publication guard from PR109 (before locking, under lock and before publication), canonical subset/accounting bijection checks, unavailable-health retention and PR113 denial parser/wire guards. Incoming alias tests and documented invariants are retained without duplicating the weaker incoming production helper. The denial executable remains hash-identical:13D523256CAD758BE13EBF96CA0209B7BB4F9A5FA1F043678DFB4867035FFB52. Earlier selective-patch provenance is historical; this candidate has both full integration parents.

Local final-code evidence:80 focused PostgreSQL16 tests and80 PostgreSQL17 tests passed;302 native dashboard/security cases passed; pinnedmypy1.19.1four source files clean. The shipped Node harness passes invalid-row ordering, future refusal, market key order, impossible partitions, previous global ledger checks and last-good retention. Preview65356bytes stays below the unchanged65536 limit. No assertion, timeout or coverage threshold is weakened. Final exact-head full PostgreSQL16/17 CI and scan evidence belong in the PR/checkpoint after completion.

PAPER-only, LIVE disabled. Engineering integration and operational PAPER remain NO-GO. LAB signatures and canonical denials are not independent production admission. Missing trusted attestor/session-root/COMMIT-coordinated revocation, publisher trust, human combined security review and genuine provider/runtime/restore evidence remain blockers. No AWS checks, deployment, migration, grants, credentials, service activation, repair/replay, real orders or G7FX work is part of this batch.

Rollback: leave the draft unmerged or revert the integration as a unit. Do not replace stricter retained guards with overlapping upstream helpers, infer zeros from absent market metadata, remove LAB refusals, or backport an unbounded reader. No production activation is authorized by this document.
