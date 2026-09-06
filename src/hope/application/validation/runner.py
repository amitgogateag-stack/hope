from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from hope.domain.validation.contexts import InvariantContext
from hope.domain.validation.invariants import INITIAL_INVARIANT_CHECKS
from hope.domain.validation.models import InvariantResult, ValidationStatus


InvariantCheck = Callable[[InvariantContext], InvariantResult]


@dataclass(frozen=True)
class InvariantRun:
    results: tuple[InvariantResult, ...]

    @property
    def passed(self) -> bool:
        return bool(self.results) and all(
            result.status is ValidationStatus.PASS for result in self.results
        )


class InvariantRunner:
    """Deterministic runner for executable domain invariants.

    Checks are ordered by their supplied order. Every check must return exactly
    one result whose invariant ID is unique. SKIP is deliberately not a pass.
    """

    def __init__(self, checks: Iterable[InvariantCheck] = INITIAL_INVARIANT_CHECKS) -> None:
        self._checks = tuple(checks)

    def run(self, context: InvariantContext) -> InvariantRun:
        results = tuple(check(context) for check in self._checks)
        ids = [result.invariant_id for result in results]
        if len(ids) != len(set(ids)):
            raise ValueError("DUPLICATE_INVARIANT_RESULT")
        return InvariantRun(results=results)
