# HOPE

**Hypothesis-driven • Observable • Proven/reproducible • Execution-aware**

HOPE is an independent Trading Research Operating System. It is not the Algo Trading Compass application.

## Safety state

- v0.1 is research/backtest/paper only.
- Live trading and broker order submission are not implemented.
- The browser is not the scheduler or trading engine.
- Historical research must be point-in-time correct.
- Research records are immutable and invalidated rather than rewritten.

## Engineering standard

`SPECIFY → DESIGN → IMPLEMENT → UNIT TEST → INTEGRATION TEST → ADVERSARIAL TEST → REGRESSION TEST → REVIEW → DOCUMENT`

The repository is being built incrementally from the HOPE v0.1 engineering specification. No strategy is promoted until the foundational correctness gates pass.
