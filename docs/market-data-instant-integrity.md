# Market-data instant integrity

Market-data intervals describe elapsed time. Every request grid, provider
boundary, duplicate key, chronological check and persisted coverage comparison
therefore uses UTC instants. Original aware timestamp values remain intact for
serialization. Existing manifest window strings, schema and content hashes are
not rewritten.

Python compares two datetimes sharing a ZoneInfo object by local wall time.
Across a daylight-saving transition this can collapse two distinct instants,
miscount an interval, or accept availability preceding its event. A New York
2026-11-01 00:00 to 03:00 hourly window contains four UTC slots; the equivalent
2026-03-08 window contains two. The two repeated 01:00 values are separate bars.

The provider validates elapsed duration, alignment, fetch closure, bounds,
completeness and echoed request identity in UTC. Normalization quarantines every
same-instant duplicate, keeps distinct fold slots and sorts by UTC chronology.
MarketBar validates event/availability/ingestion chronology using instants while
preserving the supplied timestamp representation. Lifecycle window adjacency
also uses instants before invoking providers or persistence.

Manifest parsing retains original strings and validates their canonical syntax,
then expands coverage in UTC. Finalization converts timestamps returned by
PostgreSQL before building coverage sets, so the client session timezone cannot
collapse repeated hours. Sink retry comparisons treat effective_time as an
instant: an equivalent UTC representation is a retry; a different fold is a
payload conflict. Numeric payload comparisons and database identity constraints
remain authoritative and unchanged.

Regression coverage includes spring and fall grids, equal-wall-time endpoints,
ambiguous request echoes, invalid chronology, duplicate aliases, deterministic
sorting, lifecycle gaps and overlaps, and non-UTC PostgreSQL client sessions.
The real PostgreSQL tests independently generate UTC slots and return local
provider timestamps. They verify recovery from fresh connections, concurrent
duplicate producers, exact persisted counts, interrupted sealing rollback and
successful retry, immutable manifest hashes, and effective-time conflicts.

No migration or historical data rewrite is required. Previously rejected local
transition windows can be retried against their declared manifest using the
normal staging/recovery authorization; sealed datasets remain immutable.
Strategy and risk rules, NO_TRADE, PAPER-only operation and LIVE-disabled policy
are unchanged. This batch does not connect or deploy an AWS dashboard.
