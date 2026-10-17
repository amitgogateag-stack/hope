# Post-switch dashboard acceptance

The green parent 85673e1 requires worker and dashboard observation before release
activation can succeed. An offline reproduction showed a gap: a snapshot generated
four minutes before activation, rewritten after the switch, passed the five-minute
generation tolerance. The presentation probe also treated HTTP 200 PAPER data as
available without tying its identity to the selected local export or CURRENT
status. This could accept retained or different data as new-release observation.

Release acceptance now requires both file modification and verified snapshot
generation at or after the activation start. Generation must not be in the future.
The activation captures the sanitized bounded payload and passes it, with the
activation instant, to the strict loopback probe. The probe requires CURRENT
status, matching status/snapshot generation, and equality of the complete served
snapshot to that selected payload. Equal timestamps with different counts still
fail. Rewriting an old export does not make its evidence new.

An export rotation between local read and HTTP requests may produce a temporary
identity mismatch. Existing bounded activation polling retries a fresh selection;
it never combines mismatched frames into acceptance. On acceptance timeout or
failure, existing operation locking, pointer/metadata rollback and stopped-service
review remain in force. Source and service prerequisites must be resolved before
the operator retries; the activation does not repair data or weaken evidence.

The ordinary dashboard upgrade probe keeps its independent, retained-feed
observation behavior. Strict checks are opt-in keyword arguments used by release
activation, not a schema migration or a new public endpoint. Snapshot JSON remains
32 KiB bounded and the page 64 KiB; loopback-only URLs, no redirects, sanitized
projection checks and bounded network waits remain intact.

Offline tests cover pre-switch rewritten exports, future generation, missing or
stale health, different health timestamps, different served exports, same-time
different counts, oversized/invalid selection, rollback, and actual loopback HTTP
success/replacement/retry. These prove local engineering behavior only. No AWS
host, network, service or credentials were changed. Approved staging and AWS
operator acceptance are still required; no deployment success is claimed.
