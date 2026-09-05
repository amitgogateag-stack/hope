from dataclasses import dataclass

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
