# R2: authenticated bounded file reads

The independent review showed that metadata checks alone do not establish
content authenticity. This branch adds an **optional** trusted SHA-256 digest
to `bounded_regular_read`. The bytes are still read through the pinned,
bounded, no-follow descriptor and checked against the original metadata.
When an independent expected digest is supplied, the returned bytes must
also match it, otherwise the read fails closed.

The expected digest MUST originate from an authenticated immutable manifest,
a separately trusted producer, or another reviewed trust root. Computing
the expected digest from the same untrusted file at read time proves nothing.
The existing two-argument API is unchanged for compatibility, and therefore
**legacy callers remain metadata-only** until individually migrated.

Tests cover exact digest, same-size altered contents with restored mtime,
wrong digest, invalid digest and backward compatibility. This patch does
not yet fix mutable files when no trusted expected digest exists, nor does
it solve concurrent in-place rewriting or deployment provenance.

No AWS changes, LIVE/PAPER activation, risk changes, production grants,
or frozen-baseline modifications. R2 remains OPEN until real call sites
receive independently trusted digests and adversarial concurrent mutation
tests pass.
