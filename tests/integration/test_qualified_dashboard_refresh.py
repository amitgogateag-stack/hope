"""Actual disposable restricted LOGIN and cluster-bound snapshot publication.

Fixture administrator grants are explicit test setup, not production approval.
No external signer, runtime acceptance or PAPER execution is manufactured.
"""
from datetime import datetime
import json
from pathlib import Path
import os
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from test_dashboard_reader_boundary import restricted_database
from hope.api.dashboard_snapshot import build_dashboard_snapshot
from hope.api.dashboard_snapshot_refresh import refresh_once
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.paper_handoff import database_identity
from hope.security.dashboard_observer import ObserverProfile

pytestmark=pytest.mark.integration
COMMIT='a'*40

@pytest.fixture(scope='module')
def observed_database(restricted_database):
    case=restricted_database;owner=case['engine'];role=case['reader']
    admin=create_engine(case['admin'].url.set(database=case['reader_url'].database))
    with owner.begin() as c:
        apply_migrations(c,Path(__file__).parents[2]/'migrations')
        c.exec_driver_sql('REVOKE TEMPORARY ON DATABASE "'+case['reader_url'].database+'" FROM PUBLIC')
        c.exec_driver_sql('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        c.exec_driver_sql('REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC')
        c.exec_driver_sql('GRANT USAGE ON SCHEMA public TO "'+role+'"')
        c.exec_driver_sql('GRANT SELECT ON ALL TABLES IN SCHEMA public TO "'+role+'"')
    with admin.begin() as c:
        c.exec_driver_sql('GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO "'+role+'"')
        identity=database_identity(c)
    reader=create_engine(case['reader_url'])
    try:yield dict(owner=owner,admin=admin,reader=reader,role=role,url=case['reader_url'].render_as_string(hide_password=False),profile=ObserverProfile(role,identity))
    finally:reader.dispose();admin.dispose()

def collect(case,profile=None):
    with case['reader'].connect() as c:
        result=build_dashboard_snapshot(c,deployed_commit=COMMIT,data_classification='DISPOSABLE_LAB',
            expected_database=c.engine.url.database,observer=case['profile'] if profile is None else profile)
        assert c.exec_driver_sql('SHOW transaction_read_only').scalar_one()=='on'
        assert c.exec_driver_sql('SELECT session_user=current_user').scalar_one() is True
        return result

def test_restricted_reader_observes_actual_cluster_identity_without_authority(observed_database):
    case=observed_database;value=collect(case)
    assert value['provenance']['database_identity_sha256']==case['profile'].database_sha256
    assert value['provenance']['execution_authorized'] is False
    assert value['provenance']['live_enabled'] is False

def test_successive_real_database_refreshes_are_visible_and_failed_refresh_retains_last_good(observed_database,tmp_path):
    case=observed_database;output=tmp_path/'snapshot.json';status=tmp_path/'refresh.json'
    def refresh():return refresh_once(output,status,case['url'],expected_database=case['reader'].url.database,deployed_commit=COMMIT,observer=case['profile'])
    assert refresh();first=json.loads(output.read_bytes());before=first['projections']['datasets']['registered']
    identifier=uuid4()
    try:
        with case['owner'].begin() as c:
            c.execute(text("INSERT INTO datasets(dataset_id,name,source,pit_certified) VALUES(:id,'explicit-synthetic-refresh-fixture','GENERIC',false)"),{'id':identifier})
        assert refresh();second=json.loads(output.read_bytes())
        assert second['projections']['datasets']['registered']==before+1
        assert datetime.fromisoformat(second['generated_at'])>datetime.fromisoformat(first['generated_at'])
        assert json.loads(status.read_bytes())['state']=='CURRENT'
        last=output.read_bytes()
        wrong=ObserverProfile(case['role'],'0'*64)
        assert not refresh_once(output,status,case['url'],expected_database=case['reader'].url.database,deployed_commit=COMMIT,observer=wrong)
        assert output.read_bytes()==last
        receipt=json.loads(status.read_bytes())
        assert receipt['state']=='FAILED' and receipt['error_code']=='DATABASE_IDENTITY_MISMATCH'
        assert receipt['execution_authorized'] is False and receipt['snapshot_sha256'] is None
    finally:
        with case['owner'].begin() as c:c.execute(text('DELETE FROM datasets WHERE dataset_id=:id'),{'id':identifier})

@pytest.mark.parametrize('mismatch',['principal','database','owner'])
def test_borrowed_principal_cluster_or_owner_is_not_an_observer(observed_database,mismatch):
    case=observed_database
    if mismatch=='owner':
        with case['owner'].connect() as c:
            with pytest.raises((ValueError,RuntimeError)):
                build_dashboard_snapshot(c,deployed_commit=COMMIT,data_classification='DISPOSABLE_LAB',expected_database=c.engine.url.database,observer=ObserverProfile(c.engine.url.username,case['profile'].database_sha256))
    else:
        profile=ObserverProfile('wrong_reader' if mismatch=='principal' else case['role'], '0'*64 if mismatch=='database' else case['profile'].database_sha256)
        with pytest.raises((ValueError,RuntimeError)):collect(case,profile)

@pytest.mark.parametrize('capability',['table','column','sequence','membership','temporary','definer','custom_schema'])
def test_actual_mutation_capabilities_cannot_pass_as_a_read_only_profile(observed_database,capability):
    case=observed_database;role=case['role'];suffix=uuid4().hex;name='observer_probe_'+suffix;writer='observer_writer_'+suffix
    setup=[];cleanup=[]
    if capability=='table':
        setup=['GRANT INSERT ON public.orders TO "'+role+'"'];cleanup=['REVOKE INSERT ON public.orders FROM "'+role+'"']
    elif capability=='column':
        setup=['GRANT UPDATE(cash) ON public.paper_portfolios TO "'+role+'"'];cleanup=['REVOKE UPDATE(cash) ON public.paper_portfolios FROM "'+role+'"']
    elif capability=='sequence':
        setup=['CREATE SEQUENCE public.'+name,'GRANT USAGE ON SEQUENCE public.'+name+' TO "'+role+'"'];cleanup=['DROP SEQUENCE public.'+name]
    elif capability=='temporary':
        setup=['GRANT TEMPORARY ON DATABASE "'+case['reader'].url.database+'" TO "'+role+'"'];cleanup=['REVOKE TEMPORARY ON DATABASE "'+case['reader'].url.database+'" FROM "'+role+'"']
    elif capability=='membership':
        setup=['CREATE ROLE '+writer+' NOLOGIN NOINHERIT','GRANT UPDATE(cash) ON public.paper_portfolios TO '+writer,'GRANT '+writer+' TO "'+role+'" WITH INHERIT FALSE, SET TRUE'];cleanup=['REVOKE '+writer+' FROM "'+role+'"','DROP OWNED BY '+writer,'DROP ROLE '+writer]
    elif capability=='definer':
        setup=["CREATE FUNCTION public."+name+"() RETURNS int LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'",'GRANT EXECUTE ON FUNCTION public.'+name+'() TO "'+role+'"'];cleanup=['DROP FUNCTION public.'+name+'()']
    else:
        setup=['CREATE SCHEMA '+name,'CREATE TABLE '+name+'.probe(id int)','GRANT USAGE ON SCHEMA '+name+' TO "'+role+'"','GRANT UPDATE(id) ON '+name+'.probe TO "'+role+'"'];cleanup=['DROP SCHEMA '+name+' CASCADE']
    try:
        with case['admin'].begin() as c:
            for query in setup:c.exec_driver_sql(query)
        with pytest.raises((ValueError,RuntimeError)):collect(case)
    finally:
        with case['admin'].begin() as c:
            for query in cleanup:c.exec_driver_sql(query)
    assert collect(case)['provenance']['execution_authorized'] is False

def test_missing_cluster_metadata_permission_is_not_replaced_by_database_name(observed_database):
    case=observed_database;role=case['role']
    try:
        with case['admin'].begin() as c:c.exec_driver_sql('REVOKE EXECUTE ON FUNCTION pg_catalog.pg_control_system() FROM "'+role+'"')
        with pytest.raises(ValueError,match='DASHBOARD_OBSERVER_IDENTITY_UNAVAILABLE'):collect(case)
    finally:
        with case['admin'].begin() as c:c.exec_driver_sql('GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO "'+role+'"')

def test_closing_privilege_drift_cannot_be_published(observed_database,tmp_path,monkeypatch):
    from hope.api import dashboard_snapshot as module
    case=observed_database;role=case['role'];original=module._optional_projections
    output=tmp_path/'snapshot.json';status=tmp_path/'refresh.json';output.write_bytes(b'unchanged-last-good')
    def drift(*args,**kwargs):
        result=original(*args,**kwargs)
        with case['admin'].begin() as c:c.exec_driver_sql('GRANT UPDATE(cash) ON public.paper_portfolios TO "'+role+'"')
        return result
    monkeypatch.setattr(module,'_optional_projections',drift)
    try:
        assert not refresh_once(output,status,case['url'],expected_database=case['reader'].url.database,deployed_commit=COMMIT,observer=case['profile'])
        assert output.read_bytes()==b'unchanged-last-good'
        assert json.loads(status.read_bytes())['state']=='FAILED'
    finally:
        with case['admin'].begin() as c:c.exec_driver_sql('REVOKE UPDATE(cash) ON public.paper_portfolios FROM "'+role+'"')


def test_separate_one_shot_processes_recover_after_denied_identity(observed_database,tmp_path):
    # Actual disposable PostgreSQL, not an authenticated operating service.
    case=observed_database;output=tmp_path/'snapshot.json';status=tmp_path/'snapshot-refresh.json'
    argv=[sys.executable,'-m','hope.api.dashboard_snapshot_refresh','--output',str(output),
        '--status',str(status),'--expected-database',case['reader'].url.database,
        '--deployed-commit',COMMIT,'--expected-reader',case['role'],'--expected-database-identity']
    environment=dict(os.environ,HOPE_DATABASE_URL=case['url'])
    def run(digest):
        return subprocess.run([*argv,digest],env=environment,capture_output=True,timeout=90,check=False)
    assert run(case['profile'].database_sha256).returncode==0
    first=output.read_bytes();first_time=json.loads(first)['generated_at']
    assert run('0'*64).returncode==1
    assert output.read_bytes()==first
    assert json.loads(status.read_bytes())['state']=='FAILED'
    assert run(case['profile'].database_sha256).returncode==0
    second=json.loads(output.read_bytes());receipt=json.loads(status.read_bytes())
    assert datetime.fromisoformat(second['generated_at'])>datetime.fromisoformat(first_time)
    assert receipt['state']=='CURRENT' and receipt['snapshot_generated_at']==second['generated_at']
    assert second['provenance']['execution_authorized'] is receipt['execution_authorized'] is False
