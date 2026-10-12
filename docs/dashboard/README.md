# HOPE dashboard — read-only visual prototype

Open `preview.html` locally in a browser. This is an **interactive navigation prototype**, not a deployed dashboard, database connection, or real-time monitor.

The four screens are Overview, Research Lab, PAPER Operations, and Safety & Engineering. No broker actions or market-data network calls exist in this preview. Runtime quantities are explicitly **Not connected**, never fabricated or silently displayed as zero. CI #1162 and commit 6f25849f are a fixed, dated engineering snapshot.

## Integration acceptance criteria

1. Implement a separately authenticated, read-only projection boundary for database-backed research, PAPER operations and control-plane state. Do not allow direct UI database writes.
2. Represent **unknown**, **unavailable**, **stale**, and **known zero** distinctly. Never infer a zero trade count or positive edge from missing records.
3. Show dataset provenance, strategy version, OOS evidence and cost assumptions beside any research verdict.
4. Show PAPER job status, lifecycle identity, reconciliation/audit state and portfolio accounting with explicit as-of timestamps.
5. Keep LIVE submission unavailable and risk limits unchanged. Require independent security and operational validation before any deployment.
6. Add tests for read-only behavior, missing/stale data, provenance mismatches, and unauthorized access before integrating production data.

This visual slice does not certify end-to-end trading readiness.
