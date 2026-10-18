# Market-data identity snapshots

Recovery, full ingestion/sealing and direct provider normalization capture the
requested (source, symbol) bindings before invoking provider or manifest callbacks. The
captured map is read-only and detached from the caller's map. Recovery/lifecycle
validate that snapshot and pass the same snapshot through manifest preflight,
every requested window and finalization. Per-window normalization takes its
bindings from that approved snapshot.

This prevents a provider callback, configuration reload, sink callback or
authorizer from changing an instrument identity after preflight. Previously a
mutable caller map could be approved for instrument A and later normalize the
same source symbol into instrument B. Provider output no longer controls that
binding through a shared reference.

Only keys required by the request plan are copied. Unrelated global mappings
are not enumerated. Each requested binding is looked up once; missing keys stay
missing and invalid values stay invalid, preserving existing rejection codes.
The copy does not invoke a defaultdict missing-key factory. UUID values and
source/symbol strings are immutable. This is a per-call snapshot, not a cache:
every retry captures again and must pass durable manifest authorization again.
A changed map cannot change the manifest or grant new permission.

No schema or database migration is needed. No price, PIT timestamp, freshness
threshold, session calendar, strategy or risk rule changes. Partial recovery
still never seals a dataset, exact retries remain idempotent and the full
lifecycle remains the only sealing path. Callers still own transactions and
commit/rollback. Capturing a map does not make invalid provider evidence safe.

Tests include mutation/removal during fetch, mutation between windows and
preflight/finalization, attempted writes to the read-only view, concurrent
configuration reload and bounded selective copying. PostgreSQL tests use real
manifest preflight and persistence for two distinct instrument prices, verify
the same approved identity throughout, and exercise savepoint interruption,
retry idempotency, unchanged manifest hash and lifecycle-only sealing. These
are isolated test transactions, not AWS or production database operations.
