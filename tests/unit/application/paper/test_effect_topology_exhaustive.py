"""Exhaustive PAPER lifecycle shape contract: no partial or contradictory completion."""

from itertools import combinations, permutations

import pytest

from hope.application.paper.effect_topology import (
    PaperEffectTopology,
    classify_paper_effect_topology,
)
from hope.application.paper.effects import PaperEffectType


_BASE = frozenset({
    PaperEffectType.SIGNAL, PaperEffectType.RISK, PaperEffectType.ORDER,
})
_ALLOWED = frozenset({
    _BASE | {PaperEffectType.FILL, PaperEffectType.PNL},
    _BASE | {PaperEffectType.CANCELLATION},
    _BASE | {PaperEffectType.REJECTION},
})
_TYPES = tuple(PaperEffectType)


@pytest.mark.parametrize(
    "subset",
    [
        tuple(combo)
        for count in range(len(_TYPES) + 1)
        for combo in combinations(_TYPES, count)
    ],
)
def test_all_unique_effect_subsets_complete_only_for_exact_terminal_shapes(subset):
    result = classify_paper_effect_topology(subset)
    assert (result is PaperEffectTopology.COMPLETE) == (frozenset(subset) in _ALLOWED)


@pytest.mark.parametrize("shape", sorted(_ALLOWED, key=lambda value: len(value)))
def test_complete_effect_shapes_are_order_independent(shape):
    for ordering in permutations(shape):
        assert classify_paper_effect_topology(ordering) is PaperEffectTopology.COMPLETE


@pytest.mark.parametrize("effect_type", _TYPES)
def test_repeated_effect_type_is_never_a_complete_lifecycle(effect_type):
    for allowed in _ALLOWED:
        repeated = tuple(allowed) + (effect_type,)
        assert classify_paper_effect_topology(repeated) is not PaperEffectTopology.COMPLETE


@pytest.mark.parametrize(
    "terminal",
    [
        (PaperEffectType.FILL, PaperEffectType.CANCELLATION),
        (PaperEffectType.FILL, PaperEffectType.REJECTION),
        (PaperEffectType.CANCELLATION, PaperEffectType.REJECTION),
    ],
)
def test_conflicting_terminal_outcomes_are_contradictory(terminal):
    assert classify_paper_effect_topology(
        tuple(_BASE) + terminal
    ) is PaperEffectTopology.CONTRADICTORY
