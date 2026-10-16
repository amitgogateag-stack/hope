# Recovered data at the PAPER timing boundary

Recovered and sealed data must still pass point-in-time visibility and the
existing PAPER freshness policy. Completing recovery does not make an old bar
fresh, nor make a delayed bar available before its recorded availability,
ingestion or effective instant.

PIT context construction and direct validation now compare all four timestamps
against the decision instant in UTC. Deterministic bar ordering and read-side
persisted coverage also use UTC keys. Original timestamp representations remain
unchanged. PAPER freshness selects the latest absolute event and measures its
elapsed age against the unchanged configured maximum.

Execution order submission delay and latency are elapsed durations. Timeline
construction adds them in UTC and converts back to the original timezone;
validation checks decision, order, eligibility, fill and quote instants in UTC.
A quote cannot price an explicit fill preceding its own event or availability.
Prices, quantities, commissions, slippage, strategy rules and risk thresholds
are unchanged.

Durable job identity comparisons normalize returned database schedules to UTC.
This permits an exact restart when PostgreSQL returns a repeated-hour local
timestamp equivalent to the canonical scheduled instant, while rejecting the
other fold even when its wall-clock text matches.

New PostgreSQL tests recover, retry and seal a four-slot fall-back dataset with
a delayed second-fold bar. They demonstrate stale decisions fail before claims,
strategy invocation or effects; delayed data stays invisible; a valid decision
records exactly one NO_TRADE through concurrent calls and a fresh connection
restart. A forged schedule using the other fold remains rejected. Unit tests
cover each visibility clock, freshness boundaries, spring/fall elapsed delays,
quote eligibility and unchanged valid-fill costs.

The read-only operational exporter independently bounds database connection,
statement and lock waits, preserving its last complete snapshot on timeout.
See dashboard/README.md for limits and the remaining browser freshness work.
No deployment or credential change is included. The frozen baseline and
PAPER-only/LIVE-disabled policy remain intact.
