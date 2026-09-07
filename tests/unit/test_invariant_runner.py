import pytest

from hope.application.validation.runner import InvariantRunner
from hope.domain.validation.contexts import InvariantContext
from hope.domain.validation.models import InvariantResult, ValidationStatus


def check_pass(_: InvariantContext) -> InvariantResult:
    return InvariantResult(invariant_id="I-1", status=ValidationStatus.PASS, message="ok")


def check_fail(_: InvariantContext) -> InvariantResult:
    return InvariantResult(invariant_id="I-2", status=ValidationStatus.FAIL, message="bad")


def test_runner_preserves_check_order_and_aggregate_status() -> None:
    run = InvariantRunner([check_pass, check_fail]).run(InvariantContext())
    assert [r.invariant_id for r in run.results] == ["I-1", "I-2"]
    assert run.passed is False


def valid_context() -> InvariantContext:
    return InvariantContext(
        declared_member_count=2,
        evaluated_member_count=2,
        evaluated_broker_instrument_ids=("A", "B"),
        terminal_alias_generated_positions=False,
        terminal_alias_generated_pnl=False,
        terminal_alias_coverage_count=0,
        signal_outcomes=(("NO_SIGNAL", "VALID"),),
        paper_live_order_submissions=0,
        trade_signal_ids=("s1",),
        known_signal_ids=("s1",),
        position_canonical_identity_counts=(1, 1),
        position_identity_records=(("p1", "A", 1), ("p2", "B", 1)),
        pnl_position_ids=("p1",),
        known_position_ids=("p1", "p2"),
    )


def test_default_runner_executes_all_ten_invariants() -> None:
    run = InvariantRunner().run(valid_context())
    assert len(run.results) == 10
    assert [result.invariant_id for result in run.results] == [f"INVARIANT-{i:03d}" for i in range(1, 11)]
    assert run.passed is True


def test_default_runner_fails_on_duplicate_broker_identity() -> None:
    context = valid_context()
    context = InvariantContext(**{**context.__dict__, "evaluated_broker_instrument_ids": ("A", "A")})
    run = InvariantRunner().run(context)
    result = run.results[1]
    assert result.status is ValidationStatus.FAIL
    assert run.passed is False


def test_default_runner_fails_when_no_signal_is_classified_stale() -> None:
    context = valid_context()
    context = InvariantContext(**{**context.__dict__, "signal_outcomes": (("NO_SIGNAL", "STALE_SIGNAL"),)})
    run = InvariantRunner().run(context)
    result = run.results[5]
    assert result.status is ValidationStatus.FAIL
    assert run.passed is False


def test_default_runner_does_not_treat_missing_evidence_as_pass() -> None:
    run = InvariantRunner().run(InvariantContext())
    assert all(result.status is ValidationStatus.SKIP for result in run.results)
    assert run.passed is False


def test_runner_rejects_duplicate_result_ids() -> None:
    def duplicate_one(_: InvariantContext) -> InvariantResult:
        return InvariantResult(invariant_id="I-1", status=ValidationStatus.PASS, message="ok")

    def duplicate_two(_: InvariantContext) -> InvariantResult:
        return InvariantResult(invariant_id="I-1", status=ValidationStatus.PASS, message="ok")

    with pytest.raises(ValueError, match="DUPLICATE_INVARIANT_RESULT"):
        InvariantRunner([duplicate_one, duplicate_two]).run(InvariantContext())


def test_invariant_008_rejects_unknown_trade_signal_reference() -> None:
    context = InvariantContext(trade_signal_ids=("missing",), known_signal_ids=("s1",))
    result = InvariantRunner().run(context).results[7]
    assert result.status is ValidationStatus.FAIL


def test_invariant_009_rejects_blank_identity_record() -> None:
    context = InvariantContext(position_identity_records=(("p1", None, 1),))
    result = InvariantRunner().run(context).results[8]
    assert result.status is ValidationStatus.FAIL


def test_invariant_010_rejects_unknown_pnl_position_reference() -> None:
    context = InvariantContext(pnl_position_ids=("missing",), known_position_ids=("p1",))
    result = InvariantRunner().run(context).results[9]
    assert result.status is ValidationStatus.FAIL
