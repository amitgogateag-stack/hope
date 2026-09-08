# P0-B Execution Rejection Contract

## Purpose

Define the minimal v0.1 semantics for an execution rejection without inventing broker-specific time-in-force or cancellation behavior.

## Contract

An **execution rejection** is an explicit, terminal execution outcome for a previously created order that has received **no fills**.

A valid rejection must:

- reference an existing order, signal, and instrument;
- carry a non-empty deterministic reason code;
- carry a timezone-aware rejection timestamp;
- occur at or after the order timestamp;
- leave portfolio cash and positions unchanged;
- transition the order lifecycle to `REJECTED`;
- emit attributable `EXECUTION_REJECTED` audit evidence;
- prevent any later fill from being applied to that order.

## States that are not execution rejection

The following must not be silently reclassified as rejection:

- no eligible quote has appeared yet;
- a quote exists but is not yet available at the current PIT clock;
- the input series ends while an order is still pending;
- the order crosses a market-session boundary under the current session policy;
- an order has already received a partial fill.

These remain pending/unfilled, or require a separate cancellation/time-in-force contract.

## Partial-fill boundary

An order with one or more accepted fills cannot later become `REJECTED`. Any terminal treatment of the remaining quantity after a partial fill is cancellation/time-in-force behavior and must be specified separately before implementation.

## Initial deterministic reason-code policy

The kernel accepts an explicit non-empty reason code supplied by the execution model or adapter. It does not infer broker-specific reasons. Reason codes become part of deterministic audit evidence.

## Non-goals

This contract does not define:

- DAY/GTC/IOC/FOK behavior;
- automatic rejection at end-of-series;
- automatic rejection at market close;
- broker-specific exchange rejection codes;
- cancellation of partially filled orders.

Those behaviors require separate explicit contracts.
