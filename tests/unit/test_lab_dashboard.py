"""Lab status is bounded, signed and read-only; never production authority."""
import json
import os
from pathlib import Path
import pytest
from hope.security import lab_publisher as p
from test_lab_publisher import context, signed, NOW
from test_dashboard_server import dashboard, request


@pytest.fixture
def lab_file_reader(monkeypatch):
    # Windows ChangeTime differs between lstat/fstat. Linux exercises the real
    # no-follow bounded descriptor reader. Signature validation is never mocked.
    if os.name == 'nt':
        from hope.infrastructure import durable_file
        def read(path, limit):
            raw=Path(path).read_bytes()
            if len(raw)>limit: raise ValueError('BOUNDED_READ_TOO_LARGE')
            return raw
        monkeypatch.setattr(durable_file,'bounded_regular_read',read)


def test_lab_dashboard_defaults_unavailable_and_cannot_authorize(tmp_path):
    with dashboard(tmp_path) as server:
        status,headers,raw=request(server,'/paper-lab.json')
        assert status==200 and json.loads(raw)==p.unavailable()
        assert headers['Cache-Control'].startswith('no-store')


def test_signed_lab_dashboard_denies_substitution_expiry_and_revocation(tmp_path,lab_file_reader):
    clock=[NOW];key,verifier=context(clock=lambda:clock[0])
    packet=tmp_path/'lab.json';packet.write_bytes(signed(key,verifier))
    with dashboard(tmp_path) as server:
        server.lab_verifier=verifier;server.lab_packet=packet
        view=lambda:json.loads(request(server,'/paper-lab.json')[2])
        result=view()
        assert result['state']=='LAB_OBSERVED'
        assert result['execution_authorized'] is False and result['production_admission']=='BLOCKED'
        altered=json.loads(packet.read_bytes());altered['expires_at']+=1
        packet.write_bytes(p.canonical(altered))
        assert view()==p.unavailable()
        packet.write_bytes(signed(key,verifier))
        clock[0]=NOW+10
        assert view()==p.unavailable()
        clock[0]=NOW;verifier.revoke()
        assert view()==p.unavailable()


@pytest.mark.parametrize('raw',[b'{}',b'x'*32769,b'{"private_path":"secret"}'],ids=['empty','oversized','private'])
def test_lab_reader_never_discloses_input_errors(tmp_path,lab_file_reader,raw):
    _,verifier=context();packet=tmp_path/'lab.json';packet.write_bytes(raw)
    assert p.read_observation(packet,verifier)==p.unavailable()
    assert p.read_observation(tmp_path/'absent',verifier)==p.unavailable()


def test_signed_lab_observation_cannot_change_production_readiness_endpoints(tmp_path,lab_file_reader):
    key,verifier=context()
    packet=tmp_path/'lab.json'
    packet.write_bytes(signed(key,verifier))
    with dashboard(tmp_path) as server:
        endpoints=('/publisher.json','/acceptance.json','/rehearsal.json')
        before={path:request(server,path) for path in endpoints}
        server.lab_packet=packet
        server.lab_verifier=verifier
        status,_,body=request(server,'/paper-lab.json')
        assert status==200 and json.loads(body)['state']=='LAB_OBSERVED'
        after={path:request(server,path) for path in endpoints}
        for path in endpoints:
            assert before[path][0]==after[path][0]==200
        for path in ('/acceptance.json','/rehearsal.json'):
            assert json.loads(before[path][2])['execution_authorized'] is False
            assert json.loads(after[path][2])['execution_authorized'] is False
        assert json.loads(before['/publisher.json'][2])['state']==json.loads(after['/publisher.json'][2])['state']
