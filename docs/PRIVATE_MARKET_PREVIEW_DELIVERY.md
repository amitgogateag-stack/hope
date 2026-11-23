# Private static market preview delivery

This delivery targets the existing private, loopback-only static preview, not the
activated operational dashboard. It replaces only index.html, market.html and a
sanitized market-observations.json. It does not start/restart services, import the
candidate runtime, read PostgreSQL, provision identities, install credentials,
change current release pointers or grant PAPER authority. Raw provider captures,
transport receipts, installer and approval plan never enter the served directory.

The new branch explicitly depends on unchanged draft PR124. Its full source/PR
PostgreSQL16/17 tests must pass before deployment approval. The source CI builds a
reproducible fixed-file ZIP directly from regular Git blob objects after both native
jobs pass. Dirty checkouts, text conversion, arbitrary source paths, symlink Git
objects and timestamps cannot change the fixed build's bytes unnoticed. A Windows
fixture exposed git-show stat ambiguity on long paths; cat-file reads the exact
blob instead, with no global Git setting change.

## Genuine preview build provenance

The hosted source job uses pinned actions/attest with GitHub OIDC and Sigstore to
attest the deployment ZIP. It verifies the signature, exact source/signing commit,
repository, workflow/ref and hosted-runner constraint before uploading the ZIP,
bundle and verification report. See [GitHub's artifact attestation documentation](https://docs.github.com/en/actions/concepts/security/artifact-attestations)
and [verification policy flags](https://cli.github.com/manual/gh_attestation_verify).
The deployment operator must independently verify those exact constraints and the
asset inventory; a JSON verification report alone is not trusted cryptographic
proof. No private market data is sent to public CI or the transparency log.

This is build-origin evidence for a static preview package. It is not production
Ed25519 runtime/build approval, market/PIT verification, independent human review
or A1 financial admission. Existing runtime gates remain unchanged and mandatory
for the activated dashboard and observer. Never omit activation_view when running
the candidate dashboard service. This delivery leaves that service untouched.

## Private payload and approval

Revalidate actual private captures with market_observation and export only its
sanitized projection using private_market_preview project-capture. Source quote,
history and original receipt clocks remain intact. Repackaging/browser polling
cannot make historical prices current. An absent/malformed/contradictory observation
must not produce invented prices, portfolio values, strategy results or authority.

Prepare a plan from fresh actual host file metadata, preserved snapshot hash and
canonical existing service identity and actual non-root kernel credentials/start time. Pin every old/new file
hash, exact commit, static artifact digest and private projection digest. Verify
loopback binding, existing root ownership/private permissions and code/service
identity before requesting one consolidated approval. Do not install/restart a
server or alter its binding. The installer CLI fixes the two operational paths;
there is no test-root or skip-verification command-line flag.

The existing service uses DynamicUser. Its name is not a persistent NSS account.
Identity therefore binds actual /proc PID credentials, command line and start ticks,
with closing systemd/PID checks; it does not borrow another process identity.
New sanitized static files inherit the already approved original index's root
owner/group and read-only read policy (0444/0644). No permissions are widened and
no persistent file is assigned to a recyclable dynamic GID. Raw captures remain
private outside the static directory. This preserves access after service restarts
without creating principals, stable groups or service configuration.

After explicit approval, retain original bytes/permissions in a new root-private
backup. The file transaction checks complete old state before applying, publishes
the new market JSON and page before the main link, fsyncs files/directories and
checks closing snapshot/service/file identity. Concurrent file transactions fail
on the private lock. Snapshots and all financial data are never written.

An interrupted apply retains PREPARED and original backups. Operator-directed
rollback accepts only exact original or installed states and the same approved
plan; changed bytes/permissions, symlinks, corrupt backups, changed snapshots,
changed reader/service identity and replays are refused. Interrupted rollback can
resume from known mixed states. Unknown outcomes require actual inventory first,
not blind retry, automatic overwrite or forced cleanup. Native tests use actual
POSIX files and explicitly model only the external service-identity observation.
They do not claim AWS, operating signing or independent security acceptance.

Actual AWS acceptance must use Chrome through the private tunnel, verify both
observed stock charts, original timestamps/staleness and retained historical
PostgreSQL observations. Local Chrome rehearsal is not AWS browser acceptance.
A static market JSON reread is not automatic PostgreSQL snapshot regeneration.

## Requested observation universe

The watchlist now follows the user's ten India and ten USA candidates. SPY is an
ETF listed on NYSE Arca in USD, per its [issuer](https://www.ssga.com/us/en/institutional/etfs/state-street-spdr-sp-500-etf-trust-spy).
Its provider-specific contract is not established, so its observation remains
UNAVAILABLE/PROVIDER_MAPPING_UNVERIFIED and cannot be ingested using a guessed
common-stock mapping. Index observations remain separate; SPY is not substituted
for the S&P500 index. No watchlist entry is an approved trading instrument.

PAPER-only; LIVE disabled. Independent admission, strategy eligibility and risk
thresholds are unchanged. Operational PAPER remains NO-GO. No AWS deployment or
human security approval is implied by this repository change.
