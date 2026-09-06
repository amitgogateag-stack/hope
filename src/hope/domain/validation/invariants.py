from __future__ import annotations

from dataclasses import dataclass

from hope.domain.validation.contexts import InvariantContext
from hope.domain.validation.models import InvariantResult, ValidationStatus


@dataclass(frozen=True)
class Invariant:
    invariant_id: str
    description: str


INITIAL_INVARIANTS = (
    Invariant("INVARIANT-001", "Frozen universe contains exactly the declared member count."),
    Invariant("INVARIANT-002", "No two evaluated members resolve to the same broker instrument."),
    Invariant("INVARIANT-003", "Terminal aliases cannot generate positions."),
    Invariant("INVARIANT-004", "Terminal aliases cannot generate P&L."),
    Invariant("INVARIANT-005", "Terminal aliases cannot count toward evaluated coverage."),
    Invariant("INVARIANT-006", "NO_SIGNAL cannot be classified as STALE_SIGNAL."),
    Invariant("INVARIANT-007", "Paper mode cannot submit live orders."),
    Invariant("INVARIANT-008", "Every trade must have an attributable signal."),
    Invariant("INVARIANT-009", "Every position has exactly one canonical identity."),
    Invariant("INVARIANT-010", "Every P&L record is traceable to a position."),
)


def _skip(invariant_id: str, evidence: str) -> InvariantResult:
    return InvariantResult(
        invariant_id=invariant_id,
        status=ValidationStatus.SKIP,
        message=f"REQUIRED_EVIDENCE_MISSING: {evidence}",
    )


def _result(invariant_id: str, passed: bool, message: str) -> InvariantResult:
    return InvariantResult(
        invariant_id=invariant_id,
        status=ValidationStatus.PASS if passed else ValidationStatus.FAIL,
        message=message,
    )


def check_invariant_001(context: InvariantContext) -> InvariantResult:
    if context.declared_member_count is None or context.evaluated_member_count is None:
        return _skip("INVARIANT-001", "member counts")
    return _result(
        "INVARIANT-001",
        context.declared_member_count == context.evaluated_member_count,
        f"declared={context.declared_member_count}, evaluated={context.evaluated_member_count}",
    )


def check_invariant_002(context: InvariantContext) -> InvariantResult:
    if context.evaluated_broker_instrument_ids is None:
        return _skip("INVARIANT-002", "evaluated broker instrument IDs")
    values = context.evaluated_broker_instrument_ids
    return _result("INVARIANT-002", len(values) == len(set(values)), "broker identities are unique")


def check_invariant_003(context: InvariantContext) -> InvariantResult:
    if context.terminal_alias_generated_positions is None:
        return _skip("INVARIANT-003", "terminal-alias position evidence")
    return _result("INVARIANT-003", not context.terminal_alias_generated_positions, "terminal aliases generated no positions")


def check_invariant_004(context: InvariantContext) -> InvariantResult:
    if context.terminal_alias_generated_pnl is None:
        return _skip("INVARIANT-004", "terminal-alias P&L evidence")
    return _result("INVARIANT-004", not context.terminal_alias_generated_pnl, "terminal aliases generated no P&L")


def check_invariant_005(context: InvariantContext) -> InvariantResult:
    if context.terminal_alias_coverage_count is None:
        return _skip("INVARIANT-005", "terminal-alias coverage evidence")
    return _result("INVARIANT-005", context.terminal_alias_coverage_count == 0, "terminal aliases do not count toward coverage")


def check_invariant_006(context: InvariantContext) -> InvariantResult:
    if context.signal_outcomes is None:
        return _skip("INVARIANT-006", "signal outcomes")
    bad = [(signal_id, outcome) for signal_id, outcome in context.signal_outcomes if outcome == "NO_SIGNAL_STALE"]
    return _result("INVARIANT-006", not bad, "NO_SIGNAL is not classified as STALE_SIGNAL")


def check_invariant_007(context: InvariantContext) -> InvariantResult:
    if context.paper_live_order_submissions is None:
        return _skip("INVARIANT-007", "paper live-order submission count")
    return _result("INVARIANT-007", context.paper_live_order_submissions == 0, "paper mode submitted no live orders")


def check_invariant_008(context: InvariantContext) -> InvariantResult:
    if context.trade_signal_ids is None:
        return _skip("INVARIANT-008", "trade signal references")
    bad = [signal_id for signal_id in context.trade_signal_ids if not signal_id]
    return _result("INVARIANT-008", not bad, "every trade has a signal reference")


def check_invariant_009(context: InvariantContext) -> InvariantResult:
    if context.position_canonical_identity_counts is None:
        return _skip("INVARIANT-009", "position canonical identity counts")
    bad = [count for count in context.position_canonical_identity_counts if count != 1]
    return _result("INVARIANT-009", not bad, "every position has exactly one canonical identity")


def check_invariant_010(context: InvariantContext) -> InvariantResult:
    if context.pnl_position_ids is None:
        return _skip("INVARIANT-010", "P&L position references")
    bad = [position_id for position_id in context.pnl_position_ids if not position_id]
    return _result("INVARIANT-010", not bad, "every P&L record has a position reference")


INITIAL_INVARIANT_CHECKS = (
    check_invariant_001,
    check_invariant_002,
    check_invariant_003,
    check_invariant_004,
    check_invariant_005,
    check_invariant_006,
    check_invariant_007,
    check_invariant_008,
    check_invariant_009,
    check_invariant_010,
)
