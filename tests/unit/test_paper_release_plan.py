import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from hope.api import paper_release_plan as p
from hope.api.paper_service import main as service_main
from hope.infrastructure import paper_service as service
from hope.application.jobs import create_scheduled_job_run
from datetime import UTC,datetime

SHA='a'*40;RECEIPT='b'*64;FACTORY='reviewed.factory:build'


@pytest.mark.parametrize('sha,receipt,factory',[('bad',RECEIPT,FACTORY),(SHA,'bad',FACTORY),(SHA,RECEIPT,'x:foo --once'),('7e9d341742818a97accce3e2301b1c7f96596795',RECEIPT,FACTORY)])
def test_service_plan_rejects_injection_and_frozen_release(sha,receipt,factory):
    with pytest.raises(RuntimeError):p.pinned_service(sha,receipt,factory)


def test_rendered_service_is_release_pinned_and_preserves_runtime_and_authority_boundaries():
    value=p.pinned_service(SHA,RECEIPT,FACTORY)
    assert '/current' not in value and 'systemctl' not in value
    assert value.count(f'/releases/{SHA}/deploy/operations/paper_release.py')==2
    assert value.count('--runtime-mode release')==2
    assert '--deployment-receipt '+RECEIPT in value and '--controller-directory /var/lib/hope-controller' in value
    assert 'ExecStart=\n' in value and 'ExecStartPre=\n' in value and 'HOPE_LIVE_ENABLED=false' in value
    template=(Path(__file__).parents[2]/'deploy/operations/hope-paper.service').read_text()
    assert '--deployment-receipt ${HOPE_DEPLOYMENT_RECEIPT}' in template
    assert '--controller-directory /var/lib/hope-controller' in template


def test_preparation_cli_does_not_connect_or_claim_go(tmp_path,capsys):
    (tmp_path/'001_first.sql').write_text('SELECT 1;')
    assert p.main(['--release',SHA,'--receipt',RECEIPT,'--factory',FACTORY,'--migrations-dir',str(tmp_path)])==0
    value=json.loads(capsys.readouterr().out)
    assert value['deployment_go'] is value['first_cycle_go'] is False
    assert value['migrations']['pending'] is None and value['migrations']['database_writes'] is False
    assert len(value['migrations']['inventory'])==1


def test_preparation_failure_is_bounded_without_disclosing_source(tmp_path,capsys):
    assert p.main(['--release',SHA,'--receipt',RECEIPT,'--factory','private-secret','--migrations-dir',str(tmp_path)])==1
    assert 'private-secret' not in capsys.readouterr().out


@pytest.mark.parametrize('options',[{},dict(once=True),dict(once=True,controller_directory=Path('/controller')),dict(once=True,controller_directory=Path('/controller'),deployment_receipt=RECEIPT)])
def test_single_cycle_cli_never_accepts_partial_or_malformed_authority_options(options,capsys):
    args=['--factory',FACTORY,'--migrations-dir','/release/migrations','--status-file','/status','--expected-cycle-key','invalid']
    for key,value in options.items():
        args.append('--'+key.replace('_','-'))
        if value is not True:args.append(str(value))
    with pytest.raises(SystemExit) as error:service_main(args)
    assert error.value.code==2 and 'PAPER_SINGLE_CYCLE_GUARD_REQUIRED' in capsys.readouterr().err


@pytest.mark.parametrize('certified,check,key',[(False,lambda:None,'a'*64),(True,None,'a'*64),(True,lambda:None,'bad')])
def test_single_cycle_refuses_unguarded_or_uncertified_dispatch_before_factory(certified,check,key):
    factory=Mock()
    with pytest.raises(RuntimeError,match='SINGLE_CYCLE_GUARD_REQUIRED'):
        service.run_service_cycle(None,factory,migrations_dir='/unused',now=lambda:None,require_certified=certified,execution_check=check,expected_cycle_key=key)
    factory.assert_not_called()


@pytest.mark.parametrize('once,certified,guard',[(False,True,lambda:None),(True,False,lambda:None),(True,True,None)])
def test_single_cycle_supervisor_requires_once_and_real_guard_before_publication(tmp_path,once,certified,guard):
    with pytest.raises(RuntimeError,match='SINGLE_CYCLE_GUARD_REQUIRED'):
        service.run_service(None,None,status_file=tmp_path/'health',expected_cycle_key='a'*64,once=once,require_certified=certified,cycle_guard=guard)
    assert not (tmp_path/'health').exists()


def test_logical_cycle_identity_changes_with_actual_schedule():
    first=create_scheduled_job_run('paper:controlled',datetime(2026,11,8,tzinfo=UTC))
    second=create_scheduled_job_run('paper:controlled',datetime(2026,11,9,tzinfo=UTC))
    assert service.logical_cycle_key(first)==service.logical_cycle_key(first)
    assert service.logical_cycle_key(first)!=service.logical_cycle_key(second)
    with pytest.raises(RuntimeError):service.logical_cycle_key(SimpleNamespace())


@pytest.mark.parametrize('handoff_request',['bad','a'*64])
def test_handoff_cli_refuses_missing_single_cycle_binding_before_database(handoff_request,capsys):
    args=['--factory',FACTORY,'--migrations-dir','/unused','--status-file','/unused','--handoff-request',handoff_request]
    with pytest.raises(SystemExit) as error:service_main(args)
    assert error.value.code==2 and 'PAPER_HANDOFF_REQUEST_INVALID' in capsys.readouterr().err


def test_handoff_mismatched_cycle_never_claims(monkeypatch):
    from hope.api.paper_handoff import handoff_guard
    engine=Mock();proof=SimpleNamespace(token={'first_cycle_key_sha256':'a'*64})
    with pytest.raises(RuntimeError):
        with handoff_guard(engine,None,SHA,'c'*64,RECEIPT,FACTORY,'b'*64,verifier=lambda *args:proof):pytest.fail('Wrong cycle admitted')
    engine.begin.assert_not_called()
