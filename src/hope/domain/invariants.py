from dataclasses import dataclass
from typing import Callable, Iterable


@dataclass(frozen=True, slots=True)
class InvariantResult:
    invariant_id: str
    passed: bool
    detail: str


class InvariantViolation(AssertionError):
    pass


def check_frozen_universe_count(actual: int, declared: int) -> InvariantResult:
    passed = actual == declared
    return InvariantResult("INVARIANT-001", passed, f"actual={actual}, declared={declared}")


def check_unique_canonical_ids(canonical_ids: Iterable[str]) -> InvariantResult:
    ids = list(canonical_ids)
    passed = len(ids) == len(set(ids))
    return InvariantResult(
        "INVARIANT-009",
        passed,
        "canonical identities are unique" if passed else "duplicate canonical identity",
    )


def run_invariants(checks: Iterable[Callable[[], InvariantResult]]) -> tuple[InvariantResult, ...]:
    results = tuple(check() for check in checks)
    failures = tuple(result for result in results if not result.passed)
    if failures:
        detail = "; ".join(f"{r.invariant_id}: {r.detail}" for r in failures)
        raise InvariantViolation(detail)
    return results
