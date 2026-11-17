"""Real lab signatures; synthetic keys NEVER establish production authority."""
import base64
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hope.security import lab_publisher as p

SHA = 'a' * 40
NOW = 1780000000


def context(key=None, *, clock=None, publisher='b'*32):
    key = key or Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    verifier = p.LabPublisherVerifier(public, release_commit=SHA, session='c'*32,
        publisher_instance=publisher, clock=clock or (lambda: NOW))
    return key, verifier


def payload(*, market='INDIA', scenario='NO_TRADE', result='PASS', reason='NO_SIGNAL', evidence='d'*64):
    return dict(postgres_major=16, cases=[dict(market=market, scenario=scenario,
        result=result, reason=reason, evidence_sha256=evidence,
        completed_at=datetime.fromtimestamp(NOW-1, timezone.utc).isoformat())])


def signed(key, verifier, *, sequence=1, issued=NOW, expires=None, body=None, **changes):
    value=dict(schema='hope.lab-publisher.v1', scope=p.SCOPE, key_id=verifier.key_id,
        release_commit=verifier.release_commit, session=verifier.session,
        publisher_instance=verifier.publisher_instance, challenge=verifier.challenge,
        sequence=sequence, issued_at=issued, expires_at=issued+10 if expires is None else expires,
        payload=body or payload())
    value.update(changes)
    value['signature']=base64.b64encode(key.sign(p.DOMAIN+p.canonical(value))).decode()
    return p.canonical(value)


def test_genuine_lab_signature_is_not_production_authority():
    key, verifier=context()
    result=verifier.observe(signed(key,verifier))
    assert result['state']=='LAB_OBSERVED' and result['signature']=='VALID_FOR_CONFIGURED_LAB_KEY'
    assert result['scope']=='DISPOSABLE_LAB_ONLY'
    assert result['execution_authorized'] is False and result['production_admission']=='BLOCKED'
    assert 'heartbeat_authenticated' not in result and result['state']!='VERIFIED'


@pytest.mark.parametrize('field,value', [('issued_at',NOW+1),('expires_at',NOW+15),
    ('publisher_instance','f'*32),('sequence',2),('release_commit','f'*40),
    ('challenge','0'*64),('scope','PRODUCTION_PAPER')])
def test_status_replacement_cannot_preserve_signature_and_forge_claims(field,value):
    key, verifier=context(); raw=signed(key,verifier)
    value_dict=json.loads(raw); value_dict[field]=value
    with pytest.raises(p.LabEvidenceError):verifier.observe(p.canonical(value_dict))
    assert verifier.observe(raw)['sequence']==1  # failed input never consumes watermark


def test_same_uid_peer_replaces_stale_bytes_without_freshening_the_signature(tmp_path):
    clock=[NOW]
    key, verifier=context(clock=lambda:clock[0]); raw=signed(key,verifier)
    path=tmp_path/'heartbeat.json';path.write_bytes(raw)
    assert verifier.observe(path.read_bytes())['state']=='LAB_OBSERVED'
    clock[0]=NOW+20
    script="""import json,os,sys
from pathlib import Path
p=Path(sys.argv[1]);v=json.loads(p.read_bytes());v['issued_at']+=20;v['expires_at']+=20
t=p.with_suffix('.replacement');t.write_text(json.dumps(v,sort_keys=True,separators=(',',':')))
os.replace(t,p)
print(os.getuid() if hasattr(os,'getuid') else 'windows-current-user')
"""
    peer=subprocess.run([sys.executable,'-I','-S','-c',script,str(path)],capture_output=True,text=True,check=True,timeout=10)
    if hasattr(os,'getuid'):assert int(peer.stdout)==os.getuid()
    with pytest.raises(p.LabEvidenceError,match='SIGNATURE_INVALID'):verifier.observe(path.read_bytes())
    with pytest.raises(p.LabEvidenceError,match='EXPIRED'):verifier.observe(raw)


def test_idempotent_polls_do_not_extend_signed_expiry_or_rewind_sequence():
    clock=[NOW];key,v=context(clock=lambda:clock[0]);one=signed(key,v)
    assert v.observe(one)['expires_at']==NOW+10
    clock[0]+=1
    assert v.observe(one)['expires_at']==NOW+10
    assert v.observe(signed(key,v,sequence=2,issued=NOW+1))['sequence']==2
    with pytest.raises(p.LabEvidenceError,match='REPLAY'):v.observe(one)
    with pytest.raises(p.LabEvidenceError,match='REPLAY'):v.observe(signed(key,v,sequence=2,issued=NOW+1,body=payload(result='FAIL')))
    clock[0]=NOW+20
    with pytest.raises(p.LabEvidenceError,match='EXPIRED'):v.observe(one)


def test_observer_restart_requires_a_fresh_challenge_and_rejects_old_packet():
    key,old=context();raw=signed(key,old);old.observe(raw)
    _,new=context(key)
    assert new.challenge!=old.challenge
    with pytest.raises(p.LabEvidenceError,match='CONTEXT'):new.observe(raw)
    assert new.observe(signed(key,new))['sequence']==1


def test_publisher_restart_cannot_borrow_predecessor_identity_or_nonce():
    key,old=context();_,new=context(key,publisher='e'*32)
    with pytest.raises(p.LabEvidenceError,match='CONTEXT'):new.observe(signed(key,old))
    assert new.observe(signed(key,new))['execution_authorized'] is False


def test_revoked_lab_key_and_rotated_key_do_not_reuse_old_session():
    key,v=context();raw=signed(key,v);v.observe(raw);v.revoke()
    with pytest.raises(p.LabEvidenceError,match='REVOKED'):v.observe(raw)
    other,new=context()
    with pytest.raises(p.LabEvidenceError,match='CONTEXT'):new.observe(raw)
    assert new.observe(signed(other,new))['state']=='LAB_OBSERVED'


@pytest.mark.parametrize('sequence,issued,expiry', [(True,NOW,NOW+10),(0,NOW,NOW+10),
    (2,NOW,NOW+10),(1,True,NOW+10),(1,NOW,NOW),(1,NOW,NOW+16),(1,NOW+1,NOW+10),(1,NOW-20,NOW-10)])
def test_signed_but_invalid_time_or_sequence_is_denied(sequence,issued,expiry):
    key,v=context()
    with pytest.raises(p.LabEvidenceError):v.observe(signed(key,v,sequence=sequence,issued=issued,expires=expiry))


@pytest.mark.parametrize('fault', ['duplicate_key','whitespace','missing_signature','private_field','bad_signature','wrong_key','too_large','nonfinite'])
def test_strict_packet_boundary(fault):
    key,v=context();raw=signed(key,v);doc=json.loads(raw)
    if fault=='duplicate_key':raw=raw[:-1]+b',"sequence":1}'
    elif fault=='whitespace':raw+=b' '
    elif fault=='too_large':raw=b'x'*32769
    elif fault=='nonfinite':raw=b'{"sequence":NaN}'
    elif fault=='wrong_key':raw=signed(Ed25519PrivateKey.generate(),v)
    else:
        if fault=='missing_signature':del doc['signature']
        elif fault=='private_field':doc['private_path']='must-not-leak'
        else:doc['signature']='invalid!'
        raw=p.canonical(doc)
    with pytest.raises(p.LabEvidenceError):v.observe(raw)


def test_invalid_base64_padding_fails_closed_without_consuming_watermark(tmp_path):
    key, verifier = context()
    valid = signed(key, verifier)
    document = json.loads(valid)
    document['signature'] = '!'  # binascii.Error from strict base64 decoder
    malformed = p.canonical(document)
    with pytest.raises(p.LabEvidenceError, match='PACKET_INVALID'):
        verifier.observe(malformed)
    path = tmp_path / 'lab.json'
    path.write_bytes(malformed)
    assert p.read_observation(path, verifier)['state'] == 'UNAVAILABLE'
    assert verifier.observe(valid)['sequence'] == 1


@pytest.mark.parametrize('fault', ['market','scenario','result','reason','digest','time','future','duplicate','major','extra'])
def test_even_signed_payload_cannot_escape_bounded_lab_schema(fault):
    key,v=context();body=payload();row=body['cases'][0]
    if fault in {'market','scenario','result','reason'}:row[fault]='PRIVATE_OR_OPERATIONAL_VALUE'
    elif fault=='digest':row['evidence_sha256']='invalid'
    elif fault=='time':row['completed_at']='not-a-time'
    elif fault=='future':row['completed_at']=datetime.fromtimestamp(NOW+1,timezone.utc).isoformat()
    elif fault=='duplicate':body['cases'].append(deepcopy(row))
    elif fault=='major':body['postgres_major']=True
    else:row['execution_authorized']=True
    with pytest.raises(p.LabEvidenceError):v.observe(signed(key,v,body=body))


def test_parallel_idempotent_reads_are_detached_and_do_not_grant_authority():
    key,v=context();raw=signed(key,v)
    with ThreadPoolExecutor(max_workers=8) as workers:results=list(workers.map(v.observe,[raw]*20))
    assert all(r['sequence']==1 and not r['execution_authorized'] for r in results)
    results[0]['cases'][0]['result']='FAIL'
    assert v.observe(raw)['cases'][0]['result']=='PASS'


def test_a_signed_failure_remains_a_failure_not_a_green_acceptance():
    key,v=context()
    result=v.observe(signed(key,v,body=payload(result='FAIL')))
    assert result['cases'][0]['result']=='FAIL' and result['production_admission']=='BLOCKED'


@pytest.mark.parametrize(('result','reason','completed'), [
    ('NOT_EXECUTED','BACKEND_LOST',datetime.fromtimestamp(NOW-1,timezone.utc).isoformat()),
    ('NOT_EXECUTED','NONE',None),
    ('PASS','NO_SIGNAL',None),
    ('FAIL','RECONCILIATION_FAILED',None),
    ('FAIL','NONE',datetime.fromtimestamp(NOW-1,timezone.utc).isoformat()),
])
def test_signed_case_cannot_contradict_execution_and_completion(result,reason,completed):
    key,verifier=context();body=payload(result=result,reason=reason)
    body['cases'][0]['completed_at']=completed
    with pytest.raises(p.LabEvidenceError,match='RESULT_COMPLETION_INVALID'):
        verifier.observe(signed(key,verifier,body=body))


def test_not_executed_case_never_advances_last_lab_cycle():
    key,verifier=context();body=payload(result='NOT_EXECUTED',reason='BACKEND_LOST')
    body['cases'][0]['completed_at']=None
    result=verifier.observe(signed(key,verifier,body=body))
    assert result['cases'][0]['result']=='NOT_EXECUTED'
    assert result['last_lab_cycle_at'] is None
    assert result['production_admission']=='BLOCKED' and result['execution_authorized'] is False
