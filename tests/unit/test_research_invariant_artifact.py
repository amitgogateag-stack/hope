from uuid import uuid4

from hope.application.validation.research_invariants import (
    build_research_invariant_artifact,
    invariant_context_fingerprint,
)
from hope.domain.validation.contexts import InvariantContext


def _context() -> InvariantContext:
    return InvariantContext(
        declared_member_count=1,
        evaluated_member_count=1,
        evaluated_broker_instrument_ids=("B1",),
        terminal_alias_generated_positions=False,
        terminal_alias_generated_pnl=False,
        terminal_alias_coverage_count=0,
        signal_outcomes=(("NO_SIGNAL", "VALID"),),
        paper_live_order_submissions=0,
        trade_signal_ids=("s1",),
        known_signal_ids=("s1",),
        position_canonical_identity_counts=(1,),
        position_identity_records=(("p1", "B1", 1),),
        pnl_position_ids=("p1",),
        known_position_ids=("p1",),
    )


def test_research_invariant_artifact_is_deterministic_and_complete() -> None:
    run_id = uuid4()
    first = build_research_invariant_artifact(run_id, _context())
    second = build_research_invariant_artifact(run_id, _context())

    assert first == second
    assert first.context_fingerprint == invariant_context_fingerprint(_context())
    assert [item["invariant_id"] for item in first.canonical_results] == [
        f"INVARIANT-{i:03d}" for i in range(1, 11)
    ]
    assert all(item["status"] == "PASS" for item in first.canonical_results)


def test_research_invariant_artifact_changes_when_context_changes() -> None:
    baseline = invariant_context_fingerprint(_context())
    changed = InvariantContext(
        **{**_context().__dict__, "paper_live_order_submissions": 1}
    )
    assert invariant_context_fingerprint(changed) != baseline
