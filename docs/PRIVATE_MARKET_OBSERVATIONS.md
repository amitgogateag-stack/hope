# Private market observations

This observational contract has no database, strategy factory, scheduler, broker,
financial writer or admission capability. It never turns a provider reply into
PIT certification, strategy eligibility, independent acceptance or trading authority.

## Data access and scope

Twelve Data documents `apikey=demo` for selected trial instruments:
[trial documentation](https://support.twelvedata.com/en/articles/5335783-trial),
[AAPL sample access](https://twelvedata.com/exchanges/stocks),
[INFY NSE sample access](https://twelvedata.com/exchanges/xnse).
This is limited development/trial access, not evidence of a production subscription,
redistribution permission, real-time entitlement or coverage for an entire market.
Keep raw responses, transport receipts and captures private and outside Git. No
provider response or credential belongs in the public repository. An access failure
must not trigger cookie, proxy, CAPTCHA or other access-control circumvention.

The twenty-symbol watchlist contains candidates (ten per market), not approved
index membership, current instrument registration, eligibility or tradable proxies.
Unobserved candidates and the four requested indices are explicitly UNAVAILABLE.
Never populate them with invented prices, website examples, ETFs or assumed zeros.

## Contract

`hope.api.market_observation.validated_reply` takes bounded original HTTPS response
bytes, an allowlisted market:symbol, endpoint and actual aware UTC receipt time.
It validates exchange, MIC, currency, timezone, dates, finite OHLCV, unique history
and quote accounting; it freezes only allowed fields and records both projected
and original transport SHA-256 digests. These hashes are audit references, not an
independent publisher signature. The independently stored raw transport receipt
must be retained to investigate the source; a worker-controlled hash is not proof
against same-UID substitution.

`make_market_capture` creates a release-bound private capture. `market_observation`
revalidates all captured bytes on each read. It refuses authority flags, duplicate
observations, future receipts, contradictory historical closes, changed digests,
foreign identities and malformed prices. Synthetic fixtures test the parser only.

Quote `timestamp` is the opening candle; freshness uses `last_quote_at` per the
[provider quote documentation](https://twelvedata.com/docs/market-data/quote).
Browser polling and capture generation never advance that price clock. Quotes
older than 900 seconds are STALE, including on weekends; no unapproved session
calendar excuses stale data. `is_market_open` is provider-reported at retrieval,
not an independently qualified exchange calendar. Historical daily bars retain
receipt-time availability and unverified publication/finality labels. Downloading
old bars now does not prove point-in-time availability on their trading dates.

Displayed percentage change is derived from close and previous close. A maximum
0.0001 percentage-point difference accommodates provider number representation
when checking its reported percentage. This does not change any trading or risk
threshold. OHLCV and historical quote/history disagreement remain strict.

## Functional limits and next qualification

Private diagnosis retrieved and validated two actual instrument responses and
ninety daily bars each. They were historical/stale, not a current market feed.
The source tree provides research-family contracts and a Strategy abstract class;
it does not supply a qualified concrete strategy binding for these observations.
The scanner therefore reports NOT_EVALUATED, not a fabricated NO_TRADE decision.
Portfolio positions, cash and P&L are unavailable, not zero.

An authorized provider account/export, independently qualified history/publication
calendar and instrument universe, executable validated strategy binding and
mandatory production admission remain prerequisites for research/operational
use. G7FX stays deferred. LIVE remains disabled. This contract is not a deployment
or PAPER activation approval.

## Separate read-only browser view

The fixed `/market.html` asset and `/market-observations.json` surface consume only
an operator-selected private capture (`--market-capture-file`) at the exact
server release. No request triggers network retrieval, SQL, strategy evaluation,
orders or publication. Existing loopback/Host fencing, no-store, bounded clients,
CSP, read-only methods and native private-file checks apply. Missing or rejected
captures return a sanitized UNAVAILABLE observation, never an exception/host path.

The market page shows source quote time, derived change, currency, original
history receipt time, explicit unavailable candidates, stale quotes and a chart
of actual captured daily closes. Its 60-second poll is only a file re-read.
Database snapshot generation and exporter health remain separate observations.
Invalid replacement evidence clears previously displayed market values.

Under `activation_view`, the market asset requires a distinct independently
trusted `market_preview_sha256`, while the existing page retains its own pinned
digest. The current runtime gate does not issue this extra asset evidence: managed
market-page deployment stays BLOCKED until a separately reviewed runtime update
supplies it. Never substitute a self-computed digest or omit the activation gate.
Captures remain independently UNVERIFIED, even when their containing asset is
trusted. No current-data, signer/session, PIT or PAPER readiness is implied.

Native Linux CI exercises actual private-file permissions/symlink refusal. Windows
semantic tests and disposable Chrome checks model only its incompatible native
file-metadata boundary; production reader and Linux acceptance remain unchanged.
