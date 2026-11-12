# Provider window integrity

## Demonstrated defect

`ProviderMarketDataBatch` was frozen but retained a caller-owned list. A provider
cache reload could remove a bar after constructor validation. The public
provider/normalize/persist path did not repeat completeness checks: a two-bar
request then returned one normalized bar with `safe_to_persist=True`. The sink
could append a partial window as if the provider response were complete.

The fix retains an immutable tuple of detached `RawMarketBar` snapshots, copies
the request, and revalidates the returned batch before normalization. Detaching
each bar matters because a frozen dataclass does not prevent a caller retaining
and force-mutating the same object after validation. The adapter receives a
separate request from the private expected request captured before provider
callbacks. A response which bypasses frozen fields or changes its request echo
cannot redefine what the caller requested. Bad response types, generators and
invalid bar containers fail before reaching persistence; existing tuple/list
adapter inputs remain supported. No provider is imported, selected or approved
by this change.

An ordinary caller-owned list changing later cannot change the retained complete
response. An adapter returning an already corrupted/incomplete object is refused.
This is an in-process snapshot boundary, not a signature or protection against
privileged Python code rewriting every object. Existing source, dataset,
point-in-time, market-session, immutable-version and financial gates still apply.

## Completeness without expected-grid allocation

The original completeness check materialized every expected symbol/time key,
even when a huge requested window returned no bars. Validation now checks all
received keys for the same source, requested symbols, half-open event-time
window, exact interval alignment and uniqueness. Their count must equal
`window_duration // interval * number_of_symbols`. Because every received key
is already a unique member of that finite grid, equal cardinality proves exact
coverage. A sparse huge response is rejected without constructing an enormous
expected set. Existing explicit `expected_keys` enumeration remains available
to adapters which need it; `expected_count` provides a non-materializing count.

No request-duration, resource, strategy, risk, eligibility or freshness threshold
was loosened or invented. Legitimate complete-window values and normalization
semantics remain unchanged, including timezone offsets and UTC transitions.

## Evidence and limits

Regression cases cover caller/concurrent cache mutation, detached request
identity, forged missing/duplicate/unrequested/off-grid/out-of-window bars,
unclosed-window fetch times, invalid response types/iterables and exhaustive
subsets of a multi-symbol/time grid. Synthetic India-offset and US offset-change
windows verify UTC arithmetic; they do not assert an exchange is open or provide
real provider acceptance. Huge-window tests prove completeness validation does
not enumerate unreceived keys.

Disposable PostgreSQL tests exercise the real sink, rejection before any rows,
interrupted publication rollback and exact-retry idempotency. All synthetic
dataset effects are rolled back; no production database or real data is used.

This independent branch starts from integration
`860dbba7baf9867b49744f580a6f0fcbaad7fb70`. The controller/read-only preflight work
is separately reviewable in PR #83 and is not stacked into this branch. Neither
PR is merged by the agent. AWS is untouched/unverified; LIVE remains disabled.
Approved India/USA provider/factory/datasets, independent trust artifacts and
separately authorized host/operational acceptance are still required.
