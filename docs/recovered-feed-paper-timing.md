# Recovered feed timing validation

The certified PAPER bridge must preserve the original point-in-time cutoffs
when a feed is recovered into a newly sealed vintage. Completing ingestion does
not make a bar visible at an earlier decision, or turn late information into an
executable quote. This batch adds integration coverage for these existing rules;
it does not change trading, risk, freshness or execution policy.

`tests/integration/test_certified_paper_recovered_feed_timing.py` creates a new
same-source dataset version under the existing manifest declaration, bar sink
and finalizer. It leaves the certified research vintage immutable. Every test
uses genuine PostgreSQL with the existing migrations and guards enabled, and
uses explicit synthetic research lineage and reviewer verdicts from the bridge
fixture. These are controlled test data, not live feed or research evidence.

The 23 cases cover:

- Inclusive decision and fill cutoffs for availability, ingestion and effective
  timestamps, with paired one-microsecond-late negative cases. Availability and
  ingestion retain their required temporal ordering.
- A bar exactly 120 seconds old at the configured decision cutoff, and a bar one
  microsecond older. The stale case fails before strategy evaluation, including
  a strategy that would otherwise return NO_TRADE.
- NO_TRADE retaining its existing behavior when an execution quote is not yet
  visible. No order, fill or portfolio is needed for that outcome.
- Terminal restart preserving exact durable effects, authorizations, outcomes
  and portfolio rows even when the next poll occurs after data becomes visible.
  A failed historical decision is not silently retried using later knowledge.
- Two independent workers overlapping at a real PostgreSQL lock, observed with
  pg_blocking_pids before release. Exactly one commits work; its contender skips
  the terminal run. Both FILLED and NO_TRADE paths are exercised.
- Real PostgreSQL backend termination after economic effects and before outcome
  insertion. No job, authorization, effects, outcome or portfolio survives the
  failed transaction. Retry reads identical decision inputs, commits once and
  remains stable on restart, for both FILLED and NO_TRADE paths.

No production defect was reproduced in this matrix. The engineering result is
regression protection for the interaction between sealed recovery data, decision
freshness, execution timing, atomicity and restart. It does not establish feed
quality in production, strategy edge or AWS operational readiness. No deployment,
credential exposure, database migration or LIVE activation is involved.
