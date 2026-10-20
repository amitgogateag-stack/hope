"""Aggregated recovery evidence must not expand numeric cardinality."""
from itertools import islice
from uuid import uuid4

import pytest

from hope.application.jobs import JobRunStatus
from hope.application.paper import effect_topology as topology
from hope.application.paper.effects import PaperEffectType
from hope.infrastructure.scheduling import recovery


@pytest.mark.parametrize("entry", ["evidence","cardinality"])
def test_large_recovery_count_has_constant_expansion_budget(monkeypatch,entry):
    original=topology.classify_paper_reconciliation_effect_topology
    observed=[]
    def budgeted(values):
        captured=tuple(islice(values,3))
        assert len(captured)<=2,"aggregate count expanded beyond zero/one/many budget"
        observed.append(captured)
        return original(captured)
    monkeypatch.setattr(recovery,"_classify_recovery_topology",budgeted)
    monkeypatch.setattr(topology,"classify_paper_reconciliation_effect_topology",budgeted)
    counts=((PaperEffectType.SIGNAL,10**12),)
    if entry=="evidence":
        assessment=recovery.PaperRecoveryAssessment(uuid4(),recovery.PaperRecoveryDisposition.INCOMPLETE,
            JobRunStatus.CLAIMED,frozenset({PaperEffectType.SIGNAL}),counts,True)
        assert assessment.evidence is recovery.PaperRecoveryEvidence.COMPLETE
    else:
        assert recovery._has_unambiguous_recovery_cardinality(counts) is True
    assert len(observed)==1



def test_all_small_cardinality_shapes_preserve_canonical_topology():
    from itertools import product
    kinds=tuple(PaperEffectType)
    for counts in product((0,1,2),repeat=len(kinds)):
        entries=tuple(zip(kinds,counts))
        expanded=tuple(kind for kind,count in entries for _ in range(count))
        expected=topology.classify_paper_reconciliation_effect_topology(expanded)
        assert topology.classify_paper_reconciliation_effect_counts(entries) is expected,(entries,expected)


@pytest.mark.parametrize("kind",tuple(PaperEffectType))
def test_large_counts_preserve_duplicate_classification(kind):
    small=((kind,2),)
    large=((kind,10**12),)
    assert topology.classify_paper_reconciliation_effect_counts(large) is topology.classify_paper_reconciliation_effect_counts(small)


@pytest.mark.parametrize("status,disposition",[(JobRunStatus.CLAIMED,recovery.PaperRecoveryDisposition.INCOMPLETE),
    (JobRunStatus.SUCCEEDED,recovery.PaperRecoveryDisposition.TERMINAL)])
def test_complete_fill_shape_with_duplicate_order_still_rejects_recovery(status,disposition):
    kinds=(PaperEffectType.SIGNAL,PaperEffectType.RISK,PaperEffectType.ORDER,PaperEffectType.FILL,PaperEffectType.PNL)
    counts=tuple((kind,10**12 if kind is PaperEffectType.ORDER else 1) for kind in kinds)
    assessment=recovery.PaperRecoveryAssessment(uuid4(),disposition,status,frozenset(kinds),counts,True)
    assert assessment.evidence is recovery.PaperRecoveryEvidence.CONTRADICTORY
    expected=(recovery.PaperRecoveryDecision.REQUIRE_RECONCILIATION if status is JobRunStatus.CLAIMED
              else recovery.PaperRecoveryDecision.REJECT_TERMINAL_EFFECT_CONFLICT)
    assert assessment.decision is expected
