import pytest

from hope.application.paper.effects import PaperEffectType
from hope.infrastructure.scheduling.recovery import (
    PaperRecoveryEvidence,
    _classify_recovery_evidence,
    _classify_recovery_topology,
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



def test_recovery_accepts_exact_staged_fill_accounting_topology() -> None:
    assert _classify_recovery_topology(
        (PaperEffectType.FILL, PaperEffectType.PNL)
    ).value == PaperRecoveryEvidence.COMPLETE.value


@pytest.mark.parametrize(
    "effect_types",
    [
        (PaperEffectType.FILL,),
        (PaperEffectType.PNL,),
        (PaperEffectType.FILL, PaperEffectType.FILL, PaperEffectType.PNL),
        (PaperEffectType.FILL, PaperEffectType.PNL, PaperEffectType.PNL),
    ],
)
def test_staged_fill_accounting_requires_exact_local_cardinality(
    effect_types: tuple[PaperEffectType, ...],
) -> None:
    assert _classify_recovery_topology(effect_types).value != (
        PaperRecoveryEvidence.COMPLETE.value
    )
