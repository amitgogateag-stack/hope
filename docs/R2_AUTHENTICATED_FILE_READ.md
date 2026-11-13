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

## PR99 recovery and archive-source relationship

The twelve-call inventory above describes the original PR91 boundary. PR99
migrates acceptance/rehearsal projections and the activation-gated preview.
Absent independently supplied projection digests remains UNVERIFIED. Preview
provenance comes from the separately signed release approval, not a hash of the
installed preview itself.

The preview change exposed an incomplete crash-test attestor model: its three
processes omitted release_source_sha256. All four reported PG16/17 CI jobs had
the same two failures (503/KeyError), rather than an environment-only failure.
The model now includes the new independently approved source-digest field;
native SIGKILL/rollback/503 assertions remain unchanged. Missing or malformed
production provenance still refuses BUILD_SOURCE_UNVERIFIED, without a fallback.

Release verification additionally requires the COMPLETE release/* map to match
the approval's source_sha256, using the builder/image canonical map format.
The exact bounded archive bytes hashed against the signed archive digest are
also the bytes parsed; metadata-only cache hits cannot omit that relationship.
Changed requested and unrequested files, missing source and injected extra
source are rejected even when an ephemeral test authority signs every individual
document and the archive hash is correct. Fixture source digests are computed
from the actual synthetic source map, replacing an unrelated placeholder.

Publisher heartbeat authenticity remains a separate critical requirement.
An owner-held flock/process identity does not authenticate bytes writable by a
same-UID process. A digest/sidecar derived from those same bytes supplies no
independent authority. Production acceptance requires independently authenticated,
replay-resistant publisher messages or enforced writer isolation and reviewed
consumer bindings. No such production authority is supplied by this repair.
Acceptance/rehearsal evidence and all operating readiness remain UNVERIFIED
without their independent roots; all production execution admission stays DENY.
No human security review or AWS operating acceptance is manufactured.
