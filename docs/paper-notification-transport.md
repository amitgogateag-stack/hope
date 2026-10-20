# Bounded PAPER notification transport

The notifier previously opened a blocking Unix datagram socket with no timeout.
An isolated check of the exact prior class confirmed connect happened without a
configured deadline. This batch bounds connect/send operations at one second,
retains the published fixed failure code and closes sockets on failure.
It also rejects ambiguous NUL/empty abstract addresses and oversized watchdog
numeric input before transport use. This grants no trading authority.

Adversarial tests cover timeout ordering, connect/send failure, cleanup, sanitized
errors, absent configuration, invalid input and a real saturated Unix queue on
Linux. Existing unsupported-platform and protocol tests are retained.

The first version passed local Python 3.12.14/PostgreSQL 17.11 full validation:
3295 passed, 72 platform skips, one deselection, 273.62s, coverage 89.10%.
That result predates integration of a7680e4 and e2de83f and does not establish
validation of the final composition. Exact-head Linux CI on Python 3.13 and both
PostgreSQL 16/17 remains required. Directory fsync is not supported by this
Windows local runtime; its native status-publication cases require Linux.

No AWS execution, deployment, PAPER activation, factory selection, database
migration, strategy/risk change, LIVE enablement or frozen baseline change.

Integrated focused validation on e2de83f: 49 passed, two native Unix-socket
skips, 43 deselected, 1.57s. The saturated-queue test uses an external five-second
subprocess deadline, so even removal of the production timeout cannot wedge CI.
The final full Linux matrix validates the published composition.
