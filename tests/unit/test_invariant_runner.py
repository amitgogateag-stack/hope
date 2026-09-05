from hope.application.validation.runner import InvariantRunner
from hope.domain.validation.models import InvariantResult, ValidationStatus


def check_pass(_: object) -> InvariantResult:
    return InvariantResult(invariant_id="I-1", status=ValidationStatus.PASS, message="ok")


def check_fail(_: object) -> InvariantResult:
    return InvariantResult(invariant_id="I-2", status=ValidationStatus.FAIL, message="bad")


def test_runner_preserves_check_order_and_aggregate_status() -> None:
    run = InvariantRunner([check_pass, check_fail]).run(object())
    assert [r.invariant_id for r in run.results] == ["I-1", "I-2"]
    assert run.passed is False
