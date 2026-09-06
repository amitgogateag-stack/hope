from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InvariantContext:
    """Evidence snapshot consumed by executable invariant checks.

    Fields are optional so a caller cannot accidentally turn missing evidence
    into a PASS. Missing required evidence produces SKIP, and InvariantRun
    treats SKIP as not passed.
    """

    declared_member_count: int | None = None
    evaluated_member_count: int | None = None
    evaluated_broker_instrument_ids: tuple[str, ...] | None = None

    terminal_alias_generated_positions: bool | None = None
    terminal_alias_generated_pnl: bool | None = None
    terminal_alias_coverage_count: int | None = None

    # (signal outcome, data-quality classification), e.g. ("NO_SIGNAL", "VALID").
    signal_outcomes: tuple[tuple[str, str], ...] | None = None
    paper_live_order_submissions: int | None = None
    trade_signal_ids: tuple[str | None, ...] | None = None
    position_canonical_identity_counts: tuple[int, ...] | None = None
    pnl_position_ids: tuple[str | None, ...] | None = None
