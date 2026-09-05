# HOPE v0.1 — Engineering Specification

## 0. Mission

HOPE is a Trading Research Operating System designed to determine whether trading hypotheses survive rigorous, reproducible, execution-aware validation.

HOPE is independent of Algo Trading Compass. Lessons from that system may become fixtures and regression tests, but its code, database, architecture, configuration, and runtime are not dependencies.

## 1. Non-negotiable principles

1. Correctness before convenience.
2. Reproducibility before optimization.
3. Data integrity before strategy performance.
4. Point-in-time correctness is mandatory for historical research.
5. Historical records are immutable; invalid results are invalidated, not rewritten.
6. Validation is cross-cutting infrastructure, not a final reporting step.
7. Paper mode must have no live-order capability.
8. Browser/UI is never the scheduler, trading engine, or database.
9. Every material result has complete provenance.
10. Every important production failure becomes a regression case where practical.

## 2. Architecture

Start as a modular monolith with asynchronous workers, not premature microservices.

```text
UI
 |
 API / Control Plane
 |
 Job Orchestration
 |
 +------------------------------+
 | Trading / Research Kernel     |
 | Data | Identity | Universe    |
 | Strategy | Signal | Risk      |
 | Execution | Costs | Portfolio |
 | P&L | Attribution | Diagnostics|
 +------------------------------+
 |
 Validation / Provenance (cross-cutting)
 |
 PostgreSQL + Object Storage
```

## 3. Runtime environments

RESEARCH → BACKTEST → WALK_FORWARD → PAPER → SHADOW → LIVE_READINESS → LIVE

Only RESEARCH through PAPER are in initial scope. Environment transitions are explicit and gated. LIVE is not implemented.

## 4. Technology baseline

- Python 3.13+
- PostgreSQL
- SQLAlchemy + Alembic
- Pydantic
- pytest
- NumPy / Polars / PyArrow as justified by workload
- Parquet for immutable high-volume research datasets
- Docker for reproducible runtime packaging
- TypeScript/React for the eventual UI
- Object storage abstraction for datasets/artifacts

Dependency selection must remain minimal. Do not add infrastructure because it sounds sophisticated.

## 5. Repository structure

```text
hope/
  docs/
  specs/
  configs/
  migrations/
  scripts/
  src/hope/
    domain/
      identity/
      market_data/
      universe/
      strategy/
      signal/
      risk/
      execution/
      portfolio/
      research/
      validation/
      provenance/
    application/
      experiments/
      backtests/
      paper/
      jobs/
    infrastructure/
      postgres/
      object_storage/
      market_data/
      scheduling/
    api/
  tests/
    unit/
    integration/
    regression/
    invariants/
    adversarial/
```

## 6. Domain entities

### Identity

- Instrument
- InstrumentAlias
- BrokerInstrument
- IdentityMapping
- IdentityEvent

Identity statuses:
ACTIVE, TERMINAL, DUPLICATE, AMBIGUOUS, UNRESOLVED, SUPERSEDED.

### Market data

- DataSource
- Dataset
- DatasetVersion
- MarketBar
- MarketSession
- DataQualityEvent

Important timestamps:
- event_time
- available_time
- effective_time
- ingestion_time

### Universe

- Universe
- UniverseVersion
- UniverseMember

Every universe has a PIT certification status.

### Strategy

- Strategy
- StrategyVersion
- ParameterSnapshot
- Signal

A strategy generates signals; it never directly submits orders.

### Execution

- Order
- Fill
- ExecutionModel
- CostModel
- SlippageModel

### Portfolio

- Position
- PositionEvent
- PnLEvent
- EquitySnapshot
- ExposureSnapshot
- AttributionRecord

### Research

- ResearchProject
- Hypothesis
- Experiment
- ExperimentVariant
- ExperimentRun
- Result
- ResearchDecision

### Validation

- Invariant
- InvariantRun
- RegressionCase
- TestRun
- ValidationFailure

### Provenance / audit

- Artifact
- CodeVersion
- ConfigurationSnapshot
- AuditEvent

## 7. Identity contract

No evaluated universe member may resolve to the same canonical/broker tradable identity as another evaluated member.

A duplicate/terminal alias may be retained for historical traceability but must not independently generate:

- signals
- positions
- P&L
- coverage

The KALPATPOWR-EQ → KPIL-EQ case from Algo Trading Compass becomes a generic regression fixture, not a hard-coded exception.

## 8. Data-quality contract

Required explicit states include:

VALID, MISSING, STALE, FUTURE, DUPLICATE, MALFORMED, INCOMPLETE_SESSION, INVALID_OHLC, INVALID_VOLUME, AMBIGUOUS_IDENTITY.

NO_SIGNAL is a strategy outcome, not a data-quality state.

A valid current market bar with no qualifying setup produces NO_SIGNAL.

An actually lagging dataset produces STALE_SIGNAL/data-stale state.

## 9. Point-in-time contract

Historical research may only consume information whose available_time is <= decision_time.

Current master/universe information must not silently substitute for historical information.

Every research dataset must declare whether it is PIT-certified.

## 10. Configuration and reproducibility

Experiment configuration is immutable.

Canonical configuration serialization is hashed with SHA-256.

Experiment provenance must capture:

- dataset/version
- universe/version
- strategy/version
- parameter snapshot
- cost model/version
- execution model/version
- code commit
- configuration hash
- environment
- timestamps

Same inputs + same code + same configuration must produce the same deterministic experiment definition and, where the computation is deterministic, the same result.

## 11. Strategy interface

Conceptual contract:

```python
class Strategy:
    name: str
    version: str

    def generate_signals(self, market_context, universe, parameters):
        ...
```

The interface must not expose database internals or broker APIs.

## 12. Trading kernel

```text
Market Data
  → Identity
  → Data Quality
  → Universe
  → Strategy
  → Signal
  → Risk
  → Order
  → Execution Model
  → Fill
  → Position
  → P&L
  → Attribution
  → Diagnostics
  → Audit
```

Backtest and paper execution should share as much of this kernel as practical.

## 13. Execution model

The simulator must explicitly model:

- decision timestamp
- order timestamp
- latency assumption
- available market data
- fill eligibility
- fill price
- partial fills
- rejected fills
- slippage
- transaction costs
- market/session boundaries

No hidden execution assumptions.

## 14. Risk engine

Initial architecture supports:

- position limits
- portfolio exposure
- single-name concentration
- sector concentration
- turnover limits
- daily loss limits
- data-quality halts
- execution anomaly halts
- strategy kill switch
- environment kill switch

Paper mode must be incapable of sending a live order.

## 15. Research engine

Every experiment has an immutable ID and explicit hypothesis.

Research workflow:

1. Define hypothesis.
2. Freeze control.
3. Freeze dataset/universe/configuration.
4. Create variant.
5. Run regression/invariant suite.
6. Run historical evaluation.
7. Run walk-forward.
8. Run regime analysis.
9. Run parameter sensitivity.
10. Run cost stress.
11. Run slippage stress.
12. Run universe perturbation.
13. Run contribution analysis.
14. Record decision.

Research must compare variants against predeclared controls rather than merely reporting absolute profitability.

## 16. Backtest engine

The engine is time/event aware. At each decision point it must expose only data available at that time.

It must reject or flag:

- future bars
- future universe membership
- unavailable information
- invalid sessions
- ambiguous identity
- stale required inputs

## 17. Walk-forward

Support:

TRAIN → VALIDATE → TEST → ROLL FORWARD

The exact windowing protocol is part of the experiment configuration.

## 18. Counterfactual ledger

For missed/refused signals, retain enough information to answer what would have happened under acceptance.

For executed trades, support alternative execution assumptions where feasible.

Purpose: distinguish strategy, execution, data, risk, cost, and implementation problems.

## 19. Invariants

Initial mandatory invariants:

INVARIANT-001 — Frozen universe contains exactly the declared member count.

INVARIANT-002 — No two evaluated members resolve to the same broker instrument.

INVARIANT-003 — Terminal aliases cannot generate positions.

INVARIANT-004 — Terminal aliases cannot generate P&L.

INVARIANT-005 — Terminal aliases cannot count toward evaluated coverage.

INVARIANT-006 — NO_SIGNAL cannot be classified as STALE_SIGNAL.

INVARIANT-007 — Paper mode cannot submit live orders.

INVARIANT-008 — Every trade must have an attributable signal.

INVARIANT-009 — Every position has exactly one canonical identity.

INVARIANT-010 — Every P&L record is traceable to a position.

## 20. Regression cases

Initial mandatory regression cases:

1. Duplicate broker identity: KALPATPOWR-EQ / KPIL-EQ lesson.
2. NO_SIGNAL versus STALE_SIGNAL lesson.
3. MIXED_VINTAGE_PRE_REPAIR history remains immutable and invalidatable rather than rewritten.

Regression tests must encode the generic rule, not a one-off symbol exception.

## 21. Adversarial test catalog

At minimum:

- duplicate symbols
- duplicate instruments
- missing bars
- stale bars
- future bars
- malformed bars
- out-of-order bars
- impossible OHLC
- invalid volume
- partial API response
- API outage
- duplicate signal
- duplicate order
- partial fill
- rejected fill
- strategy exception
- database interruption
- scheduler interruption
- timezone error
- holiday error
- market-close boundary
- corrupted configuration
- configuration hash mismatch

## 22. Database rules

- Use foreign keys for authoritative relationships.
- Prefer explicit status enums/check constraints for critical states.
- Use immutable/versioned records for research artifacts.
- Never update historical experiment results to repair reporting.
- Record invalidation reason and timestamp.
- Use database transactions around position/fill/P&L state transitions.

## 23. API boundary

Initial API domains:

- health
- datasets
- identity
- universes
- strategies
- experiments
- runs
- signals
- paper positions
- diagnostics
- validation
- audit

The API is a control/read layer. It is not the authoritative execution scheduler.

## 24. Worker model

Initial workers:

- market-data worker
- validation worker
- research worker
- paper worker
- P&L worker
- diagnostics worker

Workers must be restartable and idempotent where practical.

## 25. Idempotency

Repeated execution of the same scheduled job must not silently create duplicate signals, orders, fills, or P&L events.

Use durable run/job identifiers and uniqueness constraints.

## 26. Observability

Three categories:

### Operational
worker health, scheduler health, API health, DB health, data-source health.

### Trading
signals, refusals, orders, fills, positions, exposure, P&L.

### Research
data validity, PIT status, invariant status, sample size, regime coverage, cost sensitivity, stability, concentration, provenance completeness.

## 27. Security

- no secrets in source
- least-privilege DB roles
- encrypted transport/storage
- authenticated API
- authorization boundaries
- secret rotation
- dependency scanning
- container scanning
- audit logging
- no live broker credentials in v0.1

## 28. Cloud/deployment

Container-first. The browser is never required for execution.

Target topology:

```text
Internet → Web/API → Control plane → Scheduler → Workers
                                      ↓
                               PostgreSQL
                                      ↓
                               Object storage
```

All deployable artifacts are tied to code commit/container digest/configuration version.

## 29. Disaster recovery

Initial requirements:

- automated PostgreSQL backups
- object-storage versioning where available
- migration history
- restore procedure
- recovery test
- experiment artifacts preserved independently of runtime containers

## 30. UI scope

No UI dependency for M0.

Later pages may include:

/dashboard
/experiments
/runs
/signals
/trades
/positions
/strategies
/universes
/identity
/data-quality
/backtests
/walk-forward
/diagnostics
/risk
/audit
/reports

The UI must expose evidence rather than hide complexity.

## 31. M0 — Foundation milestone

M0 is complete when all of the following exist and are demonstrated:

1. Repository and deterministic Python environment.
2. PostgreSQL schema + migrations.
3. Configuration loader + canonical hash.
4. Identity domain + generic duplicate-resolution logic.
5. Data-quality domain.
6. Experiment/provenance domain.
7. First ten invariants.
8. Regression framework with initial historical lessons.
9. Adversarial test fixtures.
10. CI running unit, integration, invariant, and regression tests.
11. Paper/live safety boundary demonstrated in code.

## 32. M0 acceptance tests

- Same configuration serializes to the same hash.
- Changing one parameter changes the hash.
- Duplicate broker identity is rejected from evaluable universe membership.
- Terminal alias cannot create signal/position/P&L.
- Valid current data + no setup produces NO_SIGNAL.
- Stale data produces STALE state.
- Future data is rejected.
- Frozen universe count mismatch fails an invariant.
- Every P&L event requires a valid position.
- Every trade requires a signal reference.
- Paper environment has no live-order adapter.
- Historical invalidation does not mutate original result records.

## 33. Explicit non-goals for v0.1

- live trading
- broker order submission
- autonomous strategy optimization
- LLM strategy generation
- ML/RL strategy development
- options
- multi-asset expansion
- mobile UI
- Kubernetes
- premature microservices
- sophisticated real-time UI
- automatic promotion between environments

## 34. Definition of done

A module is complete only when:

SPECIFY → DESIGN → IMPLEMENT → UNIT TEST → INTEGRATION TEST → ADVERSARIAL TEST → REGRESSION TEST → REVIEW → DOCUMENT

"It works on my test" is not sufficient evidence.

## 35. First implementation sequence

1. Project/package configuration.
2. Domain primitives and enums.
3. Configuration/provenance hashing.
4. PostgreSQL models and migrations.
5. Identity engine.
6. Data-quality engine.
7. Experiment registry.
8. Invariant engine.
9. Regression fixtures.
10. Adversarial tests.
11. CI.

Do not implement a trading strategy until these foundations pass their gates.
