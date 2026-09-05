from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from hope.domain.validation.models import InvariantResult


InvariantCheck = Callable[[object], InvariantResult]


@dataclass(frozen=True)
class InvariantRun:
    results: tuple[InvariantResult, ...]

    @property
    def passed(self) -> bool:
        from hope.domain.validation.models import ValidationStatus
        return all(result.status is ValidationStatus.PASS for result in self.results)


class InvariantRunner:
    """Deterministic runner for domain invariants.

    Checks are ordered by their supplied order and are never silently skipped.
    """

    def __init__(self, checks: Iterable[InvariantCheck]) -> None:
        self._checks = tuple(checks)

    def run(self, context: object) -> InvariantRun:
        results = tuple(check(context) for check in self._checks)
        return InvariantRun(results=results)
