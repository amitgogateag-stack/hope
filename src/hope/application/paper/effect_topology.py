from __future__ import annotations

from collections import Counter
from enum import Enum
from typing import Iterable

from hope.application.paper.effects import PaperEffect, PaperEffectType


class PaperEffectTopology(str, Enum):
    NONE = "NONE"
    PARTIAL = "PARTIAL"
    COMPLETE = "COMPLETE"
    CONTRADICTORY = "CONTRADICTORY"


_BASE_EFFECT_TYPES = frozenset(
    {PaperEffectType.SIGNAL, PaperEffectType.RISK, PaperEffectType.ORDER}
)
_COMPLETE_EFFECT_SHAPES = frozenset(
    {
        _BASE_EFFECT_TYPES | {PaperEffectType.FILL, PaperEffectType.PNL},
        _BASE_EFFECT_TYPES | {PaperEffectType.CANCELLATION},
        _BASE_EFFECT_TYPES | {PaperEffectType.REJECTION},
    }
)


def classify_paper_effect_topology(
    effect_types: Iterable[PaperEffectType],
) -> PaperEffectTopology:
    """Classify exact durable PAPER effect topology; duplicates fail closed."""
    values = tuple(effect_types)
    if not values:
        return PaperEffectTopology.NONE
    counts = Counter(values)
    unique = frozenset(counts)
    if unique in _COMPLETE_EFFECT_SHAPES:
        if all(count == 1 for count in counts.values()):
            return PaperEffectTopology.COMPLETE
        return PaperEffectTopology.CONTRADICTORY
    if any(shape < unique for shape in _COMPLETE_EFFECT_SHAPES):
        return PaperEffectTopology.CONTRADICTORY
    terminal = unique & {
        PaperEffectType.FILL,
        PaperEffectType.CANCELLATION,
        PaperEffectType.REJECTION,
    }
    if len(terminal) > 1:
        return PaperEffectTopology.CONTRADICTORY
    return PaperEffectTopology.PARTIAL


def require_complete_paper_effects(effects: tuple[PaperEffect, ...]) -> None:
    if classify_paper_effect_topology(effect.effect_type for effect in effects) is not PaperEffectTopology.COMPLETE:
        raise ValueError("PAPER_EFFECT_TOPOLOGY_NOT_COMPLETE")
