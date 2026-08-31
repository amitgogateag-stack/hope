from dataclasses import dataclass

from .enums import Environment


class SafetyViolation(RuntimeError):
    """Raised when an operation crosses a v0.1 execution safety boundary."""


@dataclass(frozen=True, slots=True)
class ExecutionCapability:
    environment: Environment
    live_order_submission: bool = False

    def assert_safe(self) -> None:
        if self.environment is Environment.LIVE:
            raise SafetyViolation("LIVE environment is not implemented in HOPE v0.1")
        if self.live_order_submission:
            raise SafetyViolation("live order submission is forbidden in HOPE v0.1")


def paper_capability() -> ExecutionCapability:
    capability = ExecutionCapability(Environment.PAPER)
    capability.assert_safe()
    return capability
