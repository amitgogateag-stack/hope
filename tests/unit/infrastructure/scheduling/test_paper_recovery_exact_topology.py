from hope.application.paper.effects import PaperEffectType
from hope.infrastructure.scheduling.recovery import (
    PaperRecoveryEvidence,
    _classify_recovery_evidence,
)


def test_rejection_recovery_rejects_unexpected_pnl_effect() -> None:
    evidence = _classify_recovery_evidence(
        frozenset(
            {
                PaperEffectType.SIGNAL,
                PaperEffectType.RISK,
                PaperEffectType.ORDER,
                PaperEffectType.REJECTION,
                PaperEffectType.PNL,
            }
        )
    )

    assert evidence is PaperRecoveryEvidence.CONTRADICTORY


def test_cancellation_recovery_rejects_unexpected_pnl_effect() -> None:
    evidence = _classify_recovery_evidence(
        frozenset(
            {
                PaperEffectType.SIGNAL,
                PaperEffectType.RISK,
                PaperEffectType.ORDER,
                PaperEffectType.CANCELLATION,
                PaperEffectType.PNL,
            }
        )
    )

    assert evidence is PaperRecoveryEvidence.CONTRADICTORY
