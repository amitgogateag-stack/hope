# HOPE Day 29 — Canonical Snapshot

This branch records the canonical HOPE Day 29 source snapshot supplied by the project owner on 2026-08-31.

## Source integrity

- Supplied archive: `hope_v0_1_day29(1)(1).zip`
- Archive SHA-256: `917982226d141e550c09ce77fb2420944c3f0417601ee55b1620b694c988ef86`
- Extracted project files: 125 source/config/spec/test files (generated caches excluded)
- Local validation: `119 passed, 2 skipped`
- Skips: PostgreSQL integration tests because `HOPE_DATABASE_URL` is not configured in the current execution environment.

## Important

The previous experimental reconstruction on branch `m0-foundation` was superseded and must not be treated as the canonical HOPE implementation. The supplied Day 29 snapshot is the source of truth for continuation.

## Safety boundary

HOPE remains research/backtest/paper-only. Live broker integration and live order submission are not part of v0.1.

## Next engineering gate

Recover the supplied snapshot into a normal browseable Git tree without altering its contents, then continue Day 29+ work from that exact baseline. PostgreSQL integration validation remains an open gate.
