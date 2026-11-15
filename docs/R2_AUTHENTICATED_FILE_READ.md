# R2: authenticated bounded file reads

The independent review showed that metadata checks alone do not establish
content authenticity. This branch adds `authenticated_bounded_regular_read`,
which requires a trusted SHA-256 digest. The bytes are still read through the
pinned, bounded, no-follow descriptor and checked against the original
metadata. The returned bytes must also match the expected digest, otherwise
the read fails closed. The separate API prevents a security-sensitive caller
from silently downgrading to metadata-only validation by passing `None`.

The expected digest MUST originate from an authenticated immutable manifest,
a separately trusted producer, or another reviewed trust root. Computing
the expected digest from the same untrusted file at read time proves nothing.
The existing two-argument `bounded_regular_read` API is unchanged for
compatibility and explicitly remains metadata-only. Runtime attestation now
uses the authenticated entry point when an activation journal, deployment
receipt, or first-cycle authority supplies `expected_manifest`. Unpinned
diagnostics and the other production call sites remain metadata-only until
each receives independently trusted digest provenance.

Tests cover exact digest, same-size altered contents with restored mtime,
wrong digest, invalid digest, explicit downgrade refusal, backward
compatibility, and rejection of a semantically equal manifest whose raw bytes
do not match the independently pinned digest. This patch does not fix mutable
files when no trusted expected digest exists, nor does it invent deployment
provenance.

No AWS changes, LIVE/PAPER activation, risk changes, production grants,
or frozen-baseline modifications. R2 remains OPEN while other trust-boundary
call sites lack independently trusted digests.
