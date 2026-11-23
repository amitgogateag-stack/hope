# Verified provider-export adapter

`VerifiedCsvMarketDataProvider` is a non-network ingestion adapter for an
operator-supplied OHLCV export. It enables reproducible India or USA LAB
qualification when a reviewed provider export is available; it does not claim
that any provider or export has already been accepted.

The operator must independently supply the exact payload SHA-256, provider
contract SHA-256, canonical source identifier and the timestamp at which the
export was observed. The CSV retains source symbol, event, availability,
ingestion and optional effective timestamps. The adapter never substitutes the
local read time for those PIT fields.

The file must be a private, unchanged regular file within the configured byte
bound. Its digest and exact schema are checked before rows enter the existing
provider boundary. That boundary still rejects incomplete windows, duplicates,
extra symbols, off-grid times, identity-map gaps and unsafe numeric data. The
normal immutable coverage manifest, PIT universe, market calendar, dataset
sealing and PostgreSQL persistence checks remain mandatory.

A file containing more than one lifecycle window must be constructed with an
explicit immutable `request_plan`. The adapter proves that the complete file is
exactly the union of that plan before returning even the first window. Missing,
extra, duplicate, overlapping or runtime-mismatched coverage is refused. An
adapter without a plan is deliberately restricted to one lifecycle window.

When this adapter is used by the authoritative lifecycle, its payload digest,
provider-contract digest, source and observation time are embedded in the
immutable coverage manifest. Preflight, restart recovery and final sealing must
receive the same evidence. A missing or changed digest fails before provider
bytes are read or additional bars are persisted. The local pathname and byte
limit remain runtime controls and are deliberately not represented as market
provenance.

This code does **not** attest licensing, vendor authenticity, corporate-action
adjustments, symbol mapping, exchange calendars or data quality. Those require
independent review and real provider evidence. Test CSVs are synthetic fixtures
only and must never be presented as market activity. This adapter grants no
PAPER authority, bypasses no admission/exporter gate, enables no LIVE path and
does not write AWS or production databases by itself.

## Offline qualification receipt

`python -m hope.api.market_data_export_qualification` validates one or more exact
closed windows without connecting to PostgreSQL or persisting bars. Both the private
request plan and provider export require independently supplied SHA-256 digests;
the provider contract digest and observation time are mandatory. Successful
output is a canonical JSON receipt binding the immutable coverage-manifest hash,
universe, source, window and bar count. It deliberately excludes file paths and
price values, and explicitly records that no persistence or PAPER authority was
created. Version 1 single-window plans remain accepted; version 2 plans bind a
bounded ordered window list and reject overlapping logical keys before the
export is opened. A receipt is preflight evidence only: it is not provider attestation,
database acceptance, release admission or permission to trade.

## LAB PostgreSQL ingestion boundary

`ingest_verified_export_to_lab` is an application service for a separately
reviewed operator entry point. It requires an explicit existing SQLAlchemy transaction,
existing PIT-certified universe, and existing empty staging dataset version. It
hard-denies the production database name `hope`, requires an exact database name
containing `lab`, `test`, or `recovery`, and spans manifest declaration, complete
export validation, bar insertion, coverage verification, and sealing with one
savepoint. Any caught failure removes both manifest and bars. Its receipt keeps
PAPER authority, release admission, and LIVE explicitly false. The service does
not create databases, identities, credentials, datasets, universes, providers,
orders, strategies, deployment configuration, or an activation path.
