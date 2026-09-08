# HOPE

**Hypothesis-driven · Observable · Proven/reproducible · Execution-aware**

HOPE is a Trading Research Operating System. It is independent of Algo Trading Compass.

## Current milestone

**M0 — Engineering Foundation**

Initial implementation establishes deterministic domain primitives, configuration/provenance hashing, PostgreSQL schema/migrations, paper-only safety boundaries, and the test/CI foundation.

## Safety

HOPE v0.1 does **not** implement live trading or live broker order submission.

## Development

```bash
python -m venv .venv
# activate the environment
pip install -e '.[test]'
pytest
```

Optional research dependencies:

```bash
pip install -e '.[research]'
```

## Day 29 engineering hardening

Day 29 preserves the Day 28/M0 architecture and adds regression coverage around
execution lifecycle identity integrity and deterministic backtest event ordering.
The repository remains paper-only; no live broker adapter or live-order path is
introduced.
