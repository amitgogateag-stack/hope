# M2: certified research to autonomous PAPER

Baseline: `94ef7290d5766ae723cb669bd3ec8a13fd9c3745`, exact-head CI
[38969418657](https://github.com/amitgogateag-stack/hope/actions/runs/38969418657)
and issue #5 marker verified before implementation. Frozen day29 baseline
`7e9d341742818a97accce3e2301b1c7f96596795` is outside this change.

## Defect and authorization contract

A genuine PostgreSQL reproduction loaded an operational registry, committed a
RESEARCH demotion on another connection, then executed the old registry. Durable
job/effect/NO_TRADE/authorization counts were 1/1/1/0. A registry snapshot was
therefore insufficient authorization for a new claim.

Migration 232 adds a fresh database proof and immutable claim receipt. The
certified work binding acquires the existing environment-control lock followed
by the candidate-capacity advisory lock BEFORE claiming a new job. READ COMMITTED
is mandatory. Demotion and invalidation of either comparison experiment share
that lock: earlier committed revocations reject admission; later revocations
wait for the admitted transaction to commit or roll back. Promotion epochs must
match exactly. The lock covers receipt, effects and commit. Existing successful
operations remain historical truth; their receipts do not authorize new work.
No research expiration interval or scientific threshold has been invented.

## Integrated path

The production CLI unconditionally requires `CertifiedResearchPaperWork`. A
reviewed factory supplies durable strategy, market, classification, decision and
execution-dataset identities plus trusted implementation factories. The service
independently checks binding scope against the stored current classification.
Inside the write transaction the bridge independently resolves BOTH certified
research runs, immutable execution configuration, version/code/family identity,
PIT universe and dataset manifests, all nine required protocol stages, result
fingerprints and the reconstructed comparison. Matching arbitrary payload hashes
cannot substitute for certified evidence. Existing SUPPORTED reviewer decisions
remain policy; this work neither creates promotions nor changes thresholds.

Execution settings are explicit `paper_execution` configuration stored with the
approved experiment: market, portfolio UUID, initial cash, quantity, side,
existing PortfolioRiskLimits, cost-model identity, commission/slippage, latency,
submission delay and data freshness bound. Parameters must exactly match the
immutable configuration. A separately sealed execution dataset may advance the
research dataset only within its provider source and approved PIT universe.
USA instruments require NYSE/NASDAQ/AMEX/ARCA; India requires NSE/BSE.

The bounded vertical path supports zero signals or one ENTRY. Unsupported
multi-signal/exit plans fail closed. Existing strategy signal validation, risk
engine, intelligence entry gate, order kernel, latency timeline, cost-inclusive
fill simulator, accounting writers and durable identities are reused. The fill
uses an actual visible stored bar at the scheduled instant with the established
close-price quote policy; missing quotes never become invented prices.
Existing portfolios require a reviewed durable risk-context resolver for actual
session/peak equity and strategy exposure; missing anchors are unavailable.
No bearish strategy or LIVE/broker path is activated.

NO_SIGNAL and rejected entry risk both persist the existing exactly-once
NO_TRADE effect. Its canonical time remains the scheduled cycle; the immutable
233 outcome records the earlier input decision instant and specific reason.
Successful entries persist existing SIGNAL/RISK/ORDER/FILL/PNL effects and the
existing portfolio cash/position/application/P&L records. Outcome records are
observational, with database-enforced research/authorization/job/effect lineage;
they are not another accounting ledger.
Independent review found that a direct outcome insert could otherwise describe
the wrong input hash or an uncertified execution dataset. Migration 233 now
requires the actual NO_TRADE/SIGNAL input hash, exact FILLED decision instant,
sealed PIT provider-compatible data and the approved universe manifest. Negative
tests exercise these guards without disabling any database constraints.

Strict service completion independently authenticates durable execution lineage
and replays accounting verification before persisting the existing reconciliation
audit receipt. A restart may finish this observational step after committed
effects without replay. Partial claims remain blocked and use the existing
recovery classifier; complete interrupted effects use existing reconciliation.
Terminal jobs never invoke new strategy work.

## Operating boundaries and readiness

The existing explicit India/USA calendars, UTC session windows, DST tests,
closures, lateness, clock-regression, duplicate-worker and HALT contracts remain
in force. A factory must supply authoritative calendars and the necessary
historical recovery window. No feed, exchange holiday schedule, eligible live
candidate, performance-anchor resolver or AWS account is fabricated by this code.
Trusted strategy/factory Python must be reviewed and bounded, as in M1.
Low-level trusted generic PAPER APIs remain compatible for component callers;
they do not acquire a certified label or bypass the strict production CLI.

The private dashboard reads bounded authenticated projections of candidate
families/versions/markets, sealed/PIT datasets, authorization history, certified
outcomes, fill/accounting gaps and safe blocked-reason buckets. Historical
authorization never implies current full-chain eligibility. Missing permissions,
partial schemas, corrupt or partial receipts remain UNAVAILABLE or UNKNOWN.

AWS operational acceptance is pending. No AWS infrastructure, roles, credentials,
network or deployed services were modified. See `deploy/operations/README.md`
for the separately approved schema/grant/configuration and acceptance procedure.
Local deterministic fixtures are explicitly synthetic: they generate genuine
certified backtest/evaluation records under all database guards, with an explicit
test reviewer verdict. They prove engineering contracts, not trading edge.

Validation and exact-head synchronization results are recorded in
`docs/engineering-progress.md` and the completion report; code completion requires
the full regression and exact pushed-head Actions run, not this design document.

Concurrent green commits 6e55dcd and 235804e were preserved before publication.
The generic service retains its protected catalog callback. Fully certified
plans use per-claim authorization on the execution connection, avoiding an
outer advisory lock that would block that same work on a second connection.
Mixed certified/generic plans are rejected. Migration 231 additionally prevents
generic evidence from reaching candidate promotion; M2 migrations follow as
232 and 233. The merged final acceptance suite covers both service paths.
