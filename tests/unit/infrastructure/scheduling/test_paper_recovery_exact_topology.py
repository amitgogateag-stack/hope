import pytest

from hope.application.paper.effects import PaperEffectType
from hope.infrastructure.scheduling.recovery import (
    PaperRecoveryEvidence,
    _classify_recovery_evidence,
)


@pytest.mark.parametrize(
    ("effect_types", "expected"),
    [
        (
            {
                PaperEffectType.SIGNAL,
                PaperEffectType.RISK,
                PaperEffectType.ORDER,
                PaperEffectType.FILL,
                PaperEffectType.PNL,
            },
            PaperRecoveryEvidence.COMPLETE,
        ),
        (
            {
                PaperEffectType.SIGNAL,
                PaperEffectType.RISK,
                PaperEffectType.ORDER,
                PaperEffectType.CANCELLATION,
            },
            PaperRecoveryEvidence.COMPLETE,
        ),
        (
            {
                PaperEffectType.SIGNAL,
                PaperEffectType.RISK,
                PaperEffectType.ORDER,
                PaperEffectType.REJECTION,
            },
            PaperRecoveryEvidence.COMPLETE,
        ),
        (
            {
                PaperEffectType.SIGNAL,
                PaperEffectType.RISK,
                PaperEffectType.ORDER,
                PaperEffectType.FILL,
            },
            PaperRecoveryEvidence.PARTIAL,
        ),
        (
            {
                PaperEffectType.SIGNAL,
                PaperEffectType.RISK,
                PaperEffectType.ORDER,
                PaperEffectType.PNL,
            },
            PaperRecoveryEvidence.PARTIAL,
        ),
        (
            {
                PaperEffectType.SIGNAL,
                PaperEffectType.RISK,
                PaperEffectType.ORDER,
                PaperEffectType.REJECTION,
                PaperEffectType.PNL,
            },
            PaperRecoveryEvidence.CONTRADICTORY,
        ),
        (
            {
                PaperEffectType.SIGNAL,
                PaperEffectType.RISK,
                PaperEffectType.ORDER,
                PaperEffectType.CANCELLATION,
                PaperEffectType.PNL,
            },
            PaperRecoveryEvidence.CONTRADICTORY,
        ),
        (
            {
                PaperEffectType.SIGNAL,
                PaperEffectType.RISK,
                PaperEffectType.ORDER,
                PaperEffectType.CANCELLATION,
                PaperEffectType.REJECTION,
            },
            PaperRecoveryEvidence.CONTRADICTORY,
        ),
    ],
)
def test_recovery_uses_exact_canonical_terminal_topology(
    effect_types: set[PaperEffectType],
    expected: PaperRecoveryEvidence,
) -> None:
    assert _classify_recovery_evidence(frozenset(effect_types)) is expected
