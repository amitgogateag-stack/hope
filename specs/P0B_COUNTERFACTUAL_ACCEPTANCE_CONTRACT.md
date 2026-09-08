# P0-B Counterfactual Acceptance Contract

## Purpose

HOPE v0.1 requires retained refused/missed-signal evidence to support the question: what would have happened if a refused signal had been accepted?

Counterfactual evidence is research evidence only. It must never mutate the primary portfolio, primary orders, primary fills, production/paper audit history, or the strategy verdict that actually occurred.

## Initial scope

The first implementation scope is a risk-rejected ENTRY signal that already exists in the immutable backtest decision ledger.

A counterfactual run is valid only when an explicit immutable acceptance policy supplies:

- a positive hypothetical quantity;
- a positive holding period;
- `SAME_AS_PRIMARY` execution assumptions;
- `MARK_TO_HORIZON` as the initial exit assumption.

No hidden default quantity, holding period, exit rule, cost rule, or execution shortcut is permitted.

## Execution parity

When counterfactual execution is implemented, it must reuse the same primary backtest assumptions for:

- order-submission delay;
- execution latency;
- quote availability;
- fill eligibility;
- session boundaries;
- partial-fill limits;
- fill pricing;
- slippage;
- transaction costs.

A separate simplified counterfactual fill engine is prohibited.

## Isolation

Counterfactual execution must run on an isolated ledger and isolated execution lifecycle. It must not:

- change primary cash or positions;
- consume or reserve primary order/fill identifiers;
- affect later primary strategy decisions;
- alter primary risk decisions;
- appear as an actually executed trade.

Counterfactual identifiers must be deterministically namespaced from the originating decision/policy so repeated runs are reproducible.

## Initial exit assumption

`MARK_TO_HORIZON` means the hypothetical accepted position is valued at an explicit decision-time-relative holding horizon. It is diagnostic evidence, not a claim that the strategy would actually have exited there.

HOPE must not reuse a later primary strategy EXIT signal as a counterfactual exit until strategy-state forking is explicitly implemented, because accepting the refused ENTRY may change later strategy state and subsequent signals.

## Eligibility

Initial counterfactual execution is eligible only for retained risk-rejected ENTRY decisions. NO_SIGNAL, data-quality failures, identity failures, and missing strategy evaluations are not valid acceptance counterfactuals because no tradeable signal exists to accept.

## Provenance

Every counterfactual result must retain or reference:

- originating signal ID;
- originating risk decision/reason;
- policy ID and version;
- hypothetical quantity;
- holding period;
- execution/cost configuration identity;
- code/configuration provenance;
- resulting hypothetical fills and valuation evidence when implemented.

## Interpretation

Counterfactual results answer a diagnostic question about the consequence of overriding a specific refusal under declared assumptions. They are not independently promotable strategy performance and must not be mixed into primary backtest P&L or trade counts.
