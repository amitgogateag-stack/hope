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
compatibility and explicitly remains metadata-only. **All twelve production
call sites remain metadata-only** until each receives independently trusted
digest provenance and deliberately migrates to the authenticated entry point.

Tests cover exact digest, same-size altered contents with restored mtime,
wrong digest, invalid digest, explicit downgrade refusal and backward
compatibility. This patch does not yet fix mutable files when no trusted
expected digest exists, nor does it supply deployment provenance.

No AWS changes, LIVE/PAPER activation, risk changes, production grants,
or frozen-baseline modifications. R2 remains OPEN until real call sites
receive independently trusted digests and adversarial concurrent mutation
tests pass.
