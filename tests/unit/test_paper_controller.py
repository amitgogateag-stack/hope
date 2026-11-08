"""Local controller fixtures are synthetic; never operational acceptance."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

from hope.api import paper_controller as c
from hope.infrastructure.paper_service import publish_service_status

SHA = 'a' * 40
RECEIPT = 'b' * 64
REQUEST = 'c' * 64
FACTORY = 'reviewed.factory:build'
NOW = 1700000000
NATIVE = pytest.mark.skipif(os.name != 'posix', reason='Linux flock/fsync controller contract')


def verified():
    return dict(schema='hope.deployment-observation.v1', state='VERIFIED', release_commit=SHA,
                image_sha256='1'*64, container_id='2'*64, process_sha256='3'*64,
                runtime_manifest_sha256='4'*64, receipt_sha256=RECEIPT,
                host='VERIFIED', container='VERIFIED', provenance='VERIFIED', provider='VERIFIED', rollback='VERIFIED',
                expires_at=NOW+120, blockers=[], execution_authorized=False)


def staged():
    value = dict(schema=c.SCHEMA, plan=c.plan(SHA, RECEIPT, FACTORY), anchor='0'*64, events=[],
                 publisher_instance='0'*32, publisher_identity_sha256='0'*64)
    return c._append(value, 'STAGED', 'STAGED', c.digest(value['plan']), NOW)


@pytest.mark.parametrize('fault', ['state','receipt','release','scope','host','provider','rollback','runtime',
                                 'process','image','container','expiry','bool_expiry','authority','blockers','extra'])
def test_proof_rejects_incomplete_contradictory_or_expired_evidence(fault):
    proof = verified()
    if fault == 'state': proof['state'] = 'UNKNOWN'
    elif fault == 'receipt': proof['receipt_sha256'] = 'f'*64
    elif fault == 'release': proof['release_commit'] = 'f'*40
    elif fault == 'scope': proof['schema'] = 'hope.lab.v1'
    elif fault in {'host','provider','rollback'}: proof[fault] = 'UNKNOWN'
    elif fault in {'runtime','process','image','container'}:
        proof[{'runtime':'runtime_manifest_sha256','process':'process_sha256','image':'image_sha256','container':'container_id'}[fault]] = '../private'
    elif fault == 'expiry': proof['expires_at'] = NOW
    elif fault == 'bool_expiry': proof['expires_at'] = True
    elif fault == 'authority': proof['execution_authorized'] = True
    elif fault == 'blockers': proof['blockers'] = ['PAPER_BLOCKED']
    else: proof['private'] = 'secret'
    with pytest.raises(RuntimeError): c._proof(proof, c.plan(SHA, RECEIPT, FACTORY), NOW)


@pytest.mark.parametrize('fault', ['schema','sequence','bool_sequence','chain','state','code','time','expiry',
                                 'request','publisher','plan','plan_substitution','extra','halt_resume'])
def test_corrupt_journal_never_validates(fault):
    value = staged()
    value = c._append(value, 'VERIFYING', 'VERIFYING', REQUEST, NOW)
    proof, expiry = c._proof(verified(), value['plan'], NOW)
    value = c._append(value, 'VERIFIED', 'EVIDENCE_VERIFIED', REQUEST, NOW, evidence=proof, expiry=expiry)
    event = value['events'][-1]
    if fault == 'schema': value['schema'] = 'legacy'
    elif fault == 'sequence': event['sequence'] += 1
    elif fault == 'bool_sequence': event['sequence'] = True
    elif fault == 'chain': event['previous'] = 'f'*64
    elif fault == 'state': event['state'] = 'PAPER_RUNNING'
    elif fault == 'code': event['code'] = 'PRIVATE_EXCEPTION'
    elif fault == 'time': event['observed_at'] = NOW-1
    elif fault == 'expiry': event['expires_at'] = NOW+61
    elif fault == 'request': event['request'] = 'x'
    elif fault == 'publisher': value['publisher_instance'] = 'x'
    elif fault == 'plan': value['plan']['release_commit'] = '7e9d341742818a97accce3e2301b1c7f96596795'
    elif fault == 'plan_substitution': value['plan']['receipt_sha256'] = 'f'*64
    elif fault == 'extra': value['private'] = 'secret'
    else:
        value['events'][1]['state'] = 'HALTED'
        value['events'][1]['code'] = 'HALT_LATCHED'
        value['events'][1]['sha256'] = c.digest({k:v for k,v in value['events'][1].items() if k != 'sha256'})
        event['previous'] = value['events'][1]['sha256']
    event['sha256'] = c.digest({k:v for k,v in event.items() if k != 'sha256'})
    with pytest.raises(RuntimeError): c.validate(value)


def test_bounded_chain_and_safe_sequence_limit():
    value = staged()
    for index in range(40):
        value = c._append(value, 'VERIFYING', 'VERIFYING', REQUEST, NOW+index)
        value = c._append(value, 'BLOCKED', 'EVIDENCE_BLOCKED', REQUEST, NOW+index)
    assert len(value['events']) == 32 and value['events'][-1]['sequence'] == 81
    assert value['anchor'] != '0'*64
    event = value['events'][-1]
    event['sequence'] = c.MAX_SEQUENCE
    # A retained single-event chain can be authentic but cannot advance beyond
    # the exact integer range shared with browser diagnostics.
    event['sha256'] = c.digest({k:v for k,v in event.items() if k != 'sha256'})
    value['anchor'] = event['previous']; value['events'] = [event]
    c.validate(value)
    with pytest.raises(RuntimeError): c._append(value, 'VERIFYING', 'VERIFYING', REQUEST, NOW+50)


@pytest.mark.parametrize('receipt,factory', [('x',FACTORY),(RECEIPT,'os:system;private'),(RECEIPT,'a:'),
                                            (RECEIPT,'../private:build'),(RECEIPT,'é:build')])
def test_plan_inputs_are_bounded_import_free(receipt, factory):
    with pytest.raises(RuntimeError): c.plan(SHA, receipt, factory)


@NATIVE
def test_lifecycle_retry_restart_halt_and_readonly_fresh_projection(tmp_path):
    directory = tmp_path/'controller'
    c.initialize(directory, SHA, RECEIPT, FACTORY, now=NOW)
    inspector = Mock(side_effect=lambda *a,**k: verified())
    with c.controller(directory, tmp_path, inspector=inspector, clock=lambda:NOW) as owned:
        first = owned.verify(REQUEST)
        assert first['events'][-1]['state'] == 'VERIFIED'
        # Same request is safe to repeat, but NEVER replays stale verification.
        owned.verify(REQUEST)
        assert inspector.call_count == 2
        before = (directory/'state.json').read_bytes()
        result = c.observation(directory, tmp_path, inspector=inspector, now=NOW)
        assert result['state'] == 'VERIFIED' and result['expires_at'] == NOW+60
        assert result['execution_authorized'] is False
        assert (directory/'state.json').read_bytes() == before
        assert FACTORY not in json.dumps(result) and 'publisher' not in json.dumps(result)
        inspector.assert_called_with(tmp_path, SHA, expected_receipt=RECEIPT, expected_factory=FACTORY)
        assert c.observation(directory,tmp_path,inspector=inspector,now=NOW,
                             expected_plan=c.plan(SHA,RECEIPT,'other:factory'))['state']=='UNAVAILABLE'
    with c.controller(directory, tmp_path, inspector=inspector, clock=lambda:NOW) as owned:
        assert owned.value['events'][-1]['code'] == 'REVERIFY_REQUIRED'
        owned.verify(REQUEST)
        halted = owned.halt('d'*64)
        sequence = halted['events'][-1]['sequence']
        assert owned.halt('e'*64)['events'][-1]['sequence'] == sequence
        with pytest.raises(RuntimeError, match='HALT_LATCHED'): owned.verify(REQUEST)
    with c.controller(directory, tmp_path, inspector=inspector, clock=lambda:NOW) as owned:
        assert owned.value['events'][-1]['state'] == 'HALTED'
    assert c.observation(directory,tmp_path,now=NOW)['state'] == 'HALTED'


@NATIVE
@pytest.mark.parametrize('fault',['missing','corrupt','legacy','symlink','deleted','unsafe_dir','reinitialize'])
def test_invalid_or_missing_journal_cannot_be_implicitly_adopted(tmp_path,fault):
    directory = tmp_path/'controller'
    c.initialize(directory,SHA,RECEIPT,FACTORY,now=NOW)
    path=directory/'state.json'
    inspector=Mock()
    if fault in {'missing','deleted'}: path.unlink()
    elif fault=='corrupt': path.write_bytes(b'{')
    elif fault=='legacy': publish_service_status(path,{'schema':'legacy'})
    elif fault=='symlink': path.rename(directory/'other');path.symlink_to(directory/'other')
    elif fault=='unsafe_dir': directory.chmod(0o777)
    if fault=='reinitialize':
        before=path.read_bytes()
        with pytest.raises(FileExistsError):c.initialize(directory,SHA,RECEIPT,FACTORY)
        assert path.read_bytes()==before
    else:
        with pytest.raises((RuntimeError,ValueError,OSError)):
            with c.controller(directory,tmp_path,inspector=inspector):pytest.fail('invalid journal admitted')
        assert c.observation(directory,tmp_path,now=NOW)['state'] == 'UNAVAILABLE'
    inspector.assert_not_called()


@NATIVE
@pytest.mark.parametrize('fault',['expired','revoked','runtime','provider','exception','future_clock'])
def test_expiry_revocation_mismatch_and_clock_never_reuse_verified(tmp_path,fault):
    directory=tmp_path/'controller';c.initialize(directory,SHA,RECEIPT,FACTORY,now=NOW)
    proof=verified();inspector=Mock(side_effect=lambda *a,**k:deepcopy(proof))
    with c.controller(directory,tmp_path,inspector=inspector,clock=lambda:NOW) as owned:
        owned.verify(REQUEST)
        before=(directory/'state.json').read_bytes()
        if fault=='expired': now=NOW+60
        elif fault=='future_clock':now=NOW-1
        else:
            now=NOW
            if fault=='exception':inspector.side_effect=RuntimeError('private secret /host/path')
            elif fault=='revoked':proof['state']='BLOCKED'
            elif fault=='runtime':proof['runtime_manifest_sha256']='f'*64
            else:proof['provider']='UNKNOWN'
        result=c.observation(directory,tmp_path,inspector=inspector,now=now)
        assert result['state'] in {'BLOCKED','UNAVAILABLE'}
        assert 'secret' not in json.dumps(result)
        assert (directory/'state.json').read_bytes()==before


@NATIVE
def test_interruption_retains_intent_and_restart_requires_new_verification(tmp_path):
    directory=tmp_path/'controller';c.initialize(directory,SHA,RECEIPT,FACTORY,now=NOW)
    with c.controller(directory,tmp_path,inspector=Mock(side_effect=KeyboardInterrupt),clock=lambda:NOW) as owned:
        with pytest.raises(KeyboardInterrupt):owned.verify(REQUEST)
    assert c.read(directory/'state.json')['events'][-1]['state']=='VERIFYING'
    assert c.observation(directory,tmp_path,now=NOW)['code']=='RECOVERY_REQUIRED'
    with c.controller(directory,tmp_path,inspector=Mock(return_value=verified()),clock=lambda:NOW) as owned:
        assert owned.value['events'][-1]['code']=='RECOVERY_REQUIRED'
        assert owned.verify(REQUEST)['events'][-1]['state']=='VERIFIED'


@NATIVE
def test_failed_publication_poisoned_owner_never_retries_uncertain_write(tmp_path,monkeypatch):
    directory=tmp_path/'controller';c.initialize(directory,SHA,RECEIPT,FACTORY,now=NOW)
    with c.controller(directory,tmp_path,inspector=Mock(return_value=verified()),clock=lambda:NOW) as owned:
        original=c.publish_service_status
        def uncertain(path,value):
            original(path,value)
            raise OSError('injected after rename')
        monkeypatch.setattr(c,'publish_service_status',uncertain)
        with pytest.raises(OSError):owned.verify(REQUEST)
        assert owned.failed
        with pytest.raises(RuntimeError):owned.halt(REQUEST)
    assert c.read(directory/'state.json')['events'][-1]['state']=='VERIFYING'


@NATIVE
def test_concurrent_journal_substitution_and_rejected_cli_leave_owner_bytes_unchanged(tmp_path):
    directory=tmp_path/'controller';c.initialize(directory,SHA,RECEIPT,FACTORY,now=NOW)
    with c.controller(directory,tmp_path,inspector=Mock(return_value=verified()),clock=lambda:NOW) as owned:
        before=(directory/'state.json').read_bytes()
        child=subprocess.run([sys.executable,'-m','hope.api.paper_controller','verify','--directory',str(directory),
                              '--root',str(tmp_path),'--request',REQUEST],capture_output=True,text=True,timeout=10)
        assert child.returncode==1 and child.stdout.strip()=='PAPER_CONTROLLER_UNVERIFIED'
        assert (directory/'state.json').read_bytes()==before
        assert owned.verify(REQUEST)['events'][-1]['state']=='VERIFIED'
        def substitute(*a,**k):
            publish_service_status(directory/'state.json',staged())
            return verified()
        owned.inspector=substitute
        with pytest.raises(RuntimeError):owned.verify(REQUEST)
        assert c.read(directory/'state.json')['events'][-1]['state']=='STAGED'


@NATIVE
def test_actual_sigkill_preserves_intent_and_releases_exclusive_owner(tmp_path):
    directory=tmp_path/'controller';c.initialize(directory,SHA,RECEIPT,FACTORY,now=NOW)
    script='''
import sys,time
from hope.api.paper_controller import controller
def slow(*a,**k):
    print('VERIFYING',flush=True)
    time.sleep(60)
with controller(sys.argv[1],sys.argv[2],inspector=slow,clock=lambda:1700000000) as owner:
    owner.verify('c'*64)
'''
    child=subprocess.Popen([sys.executable,'-c',script,str(directory),str(tmp_path)],stdout=subprocess.PIPE,text=True)
    try:
        import selectors
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout,selectors.EVENT_READ)
            assert selector.select(10),'child failed to reach durable intent'
            assert child.stdout.readline().strip()=='VERIFYING'
        child.kill();child.wait(timeout=10)
        with c.controller(directory,tmp_path,inspector=Mock(return_value=verified()),clock=lambda:NOW) as owner:
            assert owner.value['events'][-1]['code']=='RECOVERY_REQUIRED'
            assert owner.verify(REQUEST)['events'][-1]['state']=='VERIFIED'
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=10)
        child.stdout.close()


@NATIVE
@pytest.mark.parametrize('phase',['inspect','publish'])
def test_shared_activation_fence_covers_inspection_and_durable_commit(tmp_path,monkeypatch,phase):
    directory=tmp_path/'controller';c.initialize(directory,SHA,RECEIPT,FACTORY,now=NOW)
    original=c.publish_service_status
    calls=[]
    def competing_activation():
        script='''
import sys
from pathlib import Path
from hope.api.operations_deployment import _activation_guard
try:
    with _activation_guard(Path(sys.argv[1])):sys.exit(7)
except RuntimeError:sys.exit(0)
'''
        child=subprocess.run([sys.executable,'-c',script,str(tmp_path)],capture_output=True,text=True,timeout=10)
        assert child.returncode==0,child.stderr
        calls.append(True)
    def inspect(*a,**k):
        if phase=='inspect':competing_activation()
        return verified()
    def publish(path,value):
        if phase=='publish' and value['events'][-1]['state']=='VERIFIED':competing_activation()
        original(path,value)
    monkeypatch.setattr(c,'publish_service_status',publish)
    with c.controller(directory,tmp_path,inspector=inspect,clock=lambda:NOW) as owner:
        assert owner.verify(REQUEST)['events'][-1]['state']=='VERIFIED'
    assert len(calls)==1


@NATIVE
def test_lost_controller_lease_cannot_publish_over_successor(tmp_path):
    directory=tmp_path/'controller';c.initialize(directory,SHA,RECEIPT,FACTORY,now=NOW)
    with c.controller(directory,tmp_path,inspector=Mock(return_value=verified()),clock=lambda:NOW) as owner:
        lock=directory/'state.json.history.lock'
        lock.unlink()
        with c.controller(directory,tmp_path,inspector=Mock(return_value=verified()),clock=lambda:NOW) as successor:
            successor.verify(REQUEST)
            before=(directory/'state.json').read_bytes()
            with pytest.raises(RuntimeError):owner.halt(REQUEST)
            with pytest.raises(RuntimeError):owner.verify(REQUEST)
            assert (directory/'state.json').read_bytes()==before


@NATIVE
def test_missing_real_deployment_evidence_blocks_and_does_not_import_factory(tmp_path):
    directory=tmp_path/'controller';c.initialize(directory,SHA,RECEIPT,'never_import_this:factory',now=NOW)
    with c.controller(directory,tmp_path,clock=lambda:NOW) as owner:
        assert owner.verify(REQUEST)['events'][-1]['code']=='EVIDENCE_BLOCKED'
    assert 'never_import_this' not in sys.modules
    result=c.observation(directory,tmp_path,now=NOW)
    assert result['state']=='BLOCKED' and result['execution_authorized'] is False


@NATIVE
def test_observation_rejects_journal_change_during_fresh_evidence_collection(tmp_path):
    directory=tmp_path/'controller';c.initialize(directory,SHA,RECEIPT,FACTORY,now=NOW)
    with c.controller(directory,tmp_path,inspector=Mock(return_value=verified()),clock=lambda:NOW) as owner:
        owner.verify(REQUEST)
        def racing(*a,**k):
            owner.halt(REQUEST)
            return verified()
        assert c.observation(directory,tmp_path,inspector=racing,now=NOW)['state']=='BLOCKED'
    assert c.observation(directory,tmp_path,now=NOW)['state']=='HALTED'


@NATIVE
def test_cli_safe_output_initialization_halt_and_inspect(tmp_path,capsys):
    directory=tmp_path/'controller'
    common=['--directory',str(directory),'--root',str(tmp_path)]
    assert c.main(['initialize',*common,'--release',SHA,'--receipt',RECEIPT,'--factory',FACTORY])==0
    assert json.loads(capsys.readouterr().out)['state']=='STAGED'
    assert c.main(['verify',*common,'--request',REQUEST])==1
    assert json.loads(capsys.readouterr().out)['state']=='BLOCKED'
    assert c.main(['halt',*common,'--request',REQUEST])==0
    assert json.loads(capsys.readouterr().out)['state']=='HALTED'
    assert c.main(['inspect',*common])==0
    assert json.loads(capsys.readouterr().out)['state']=='HALTED'
    before=(directory/'state.json').read_bytes()
    assert c.main(['initialize',*common,'--release',SHA,'--receipt',RECEIPT,'--factory',FACTORY])==1
    assert capsys.readouterr().out.strip()=='PAPER_CONTROLLER_UNVERIFIED'
    assert (directory/'state.json').read_bytes()==before


@NATIVE
@pytest.mark.parametrize('closing',[NOW+60,NOW-1])
def test_live_observation_closes_clock_window_after_slow_inspection(tmp_path,monkeypatch,closing):
    directory=tmp_path/'controller';c.initialize(directory,SHA,RECEIPT,FACTORY,now=NOW)
    with c.controller(directory,tmp_path,inspector=Mock(return_value=verified()),clock=lambda:NOW) as owner:
        owner.verify(REQUEST)
        clock=iter([NOW,closing]);monkeypatch.setattr(c.time,'time',lambda:next(clock))
        result=c.observation(directory,tmp_path,inspector=Mock(return_value=verified()))
        assert result['state']=='BLOCKED' and result['code']=='REVERIFY_REQUIRED'
