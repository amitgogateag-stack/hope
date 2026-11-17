"""Clock rollback cannot revive an already expired laboratory heartbeat."""
import pytest
from hope.security.lab_publisher import LabEvidenceError
from test_lab_publisher import context, signed, NOW


@pytest.mark.parametrize('observe_first',[False,True])
def test_expired_signed_packet_cannot_revive_after_observer_clock_rewinds(observe_first):
    clock=[NOW];key,verifier=context(clock=lambda:clock[0]);packet=signed(key,verifier)
    if observe_first: assert verifier.observe(packet)['sequence']==1
    clock[0]=NOW+11
    with pytest.raises(LabEvidenceError,match='EXPIRED'):verifier.observe(packet)
    clock[0]=NOW+1
    with pytest.raises(LabEvidenceError,match='CLOCK_REWOUND'):verifier.observe(packet)


@pytest.mark.parametrize('bad',[True,None,float('nan'),float('inf'),'1780000000'])
def test_invalid_observer_clock_is_denied_without_consuming_sequence(bad):
    clock=[bad];key,verifier=context(clock=lambda:clock[0]);packet=signed(key,verifier)
    with pytest.raises(LabEvidenceError,match='CLOCK_INVALID'):verifier.observe(packet)
    clock[0]=NOW
    assert verifier.observe(packet)['sequence']==1
