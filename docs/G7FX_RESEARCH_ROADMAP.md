# G7FX: PIT-safe VWAP and Volume Profile research

**Verified status: NOT STARTED.** No G7FX implementation, calculation results,
research branch, walk-forward evidence or independent qualification was found in
the repository assessment for the November12 M4 directive. This document retains
the research priority; it is not research evidence or PAPER eligibility.

## Priority and independent work boundary

M4 security acceptance takes precedence. After the current A1 implementation and
its native/full validation, research can be isolated on a future
`codex/g7fx-pit-research` branch from a verified research-compatible baseline.
That branch has NOT been created by this batch. Pure calculation/PIT tests with
explicit synthetic research fixtures can proceed independently without touching
the admission, financial, scheduler, broker, risk or validated-strategy code.
Real-data research remains dependent on properly licensed, versioned and accepted
India/USA datasets and calendars. It must not consume the release-critical team's
security validation capacity or claim synthetic data as operational acceptance.

No G7FX operational PAPER integration, strategy registration, bearish execution
activation or LIVE capability is authorized. Existing validated strategy logic
and risk thresholds must remain unchanged. Separate approval is required before
any trading activation, even after research qualification.

## Required research design

1. **Market/session identity:** India and USA are separate market contracts.
   Use versioned exchange-local calendars, timezone-aware timestamps, DST where
   applicable, holidays, shortened sessions and explicitly selected auction or
   extended-hours coverage. Session boundaries and data cutoffs must be known at
   each research decision; never infer a market's session from another market.
2. **Point-in-time integrity:** Each observation/revision must have source event
   time and availability time. Only data available by the decision cutoff may be
   read. Preserve immutable dataset versions, source provenance, corporate-action
   knowledge dates and reproducible run/config/code identities. Future session
   totals, later bar revisions and full-sample normalization are forbidden inputs.
3. **VWAP correctness:** Independently cross-check cumulative price-times-volume
   divided by cumulative volume, using only the available session prefix. Specify
   the price/volume convention, reset rules, zero-volume behavior and numerical
   precision. Bar-derived approximations must be identified as approximations,
   rather than represented as trade-level VWAP.
4. **Volume Profile correctness:** Fix price-bin construction using ex-ante inputs;
   specify tick/lot normalization, allocation, volume conservation, POC tie rules
   and value-area rules. Distinguish session-to-date profiles from prior completed
   sessions. The current day's future high/low or total volume cannot set bins or
   profile levels used at an earlier decision.
5. **Scenario coverage:** Include bullish, bearish and neutral conditions separately
   for both markets, plus gaps, missing/stale bars, zero volume, session rollover,
   out-of-order revisions and liquidity stress. A neutral/no-signal outcome is valid.
   Bearish research is not authorization for operational short exposure.
6. **Execution realism:** Apply declared transaction costs, commissions/fees,
   bid/ask spread, slippage, latency, participation/liquidity limits and fill timing.
   Signals may not fill at prices unavailable after their decision. Use market-
   appropriate assumptions and sensitivity analysis; reject optimistic fill bias.
7. **Validation:** Time-ordered walk-forward development and locked out-of-sample
   evaluation, explicit training/validation boundaries, leakage checks, parameter
   stability, regime-specific performance and stressed execution costs. Preserve
   rejected runs and avoid repeated tuning on the test sample.
8. **Qualification:** Independent calculation review, reproducible PIT tests and
   independent strategy qualification precede any PAPER eligibility decision.
   Existing certification, risk, sizing, reconciliation, freshness and admission
   requirements remain mandatory; research cannot waive them.

## Evidence-based status transitions

- **NOT STARTED:** roadmap/assessment only; no implemented research results.
- **RESEARCH IN PROGRESS:** an actual isolated research branch contains executable
  calculations/tests or reproducible experiments; report exact evidence and gaps.
- **VALIDATED:** required market/session/PIT/calculation/execution and out-of-sample
  proofs are complete and independently qualified. This still grants no trading
  activation and does not establish operational PAPER readiness.

Report G7FX separately from M4. Do not infer a status upgrade from ordinary code
CI, this roadmap, an isolated proxy proof or another strategy's validation.
