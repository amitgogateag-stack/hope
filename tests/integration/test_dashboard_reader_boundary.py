"""Actual non-CREATEROLE DB owner and independent LOGIN, disposable PG only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
from pathlib import Path
import secrets
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine,text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from dashboard_reader_fixture import restricted_dashboard_reader,TABLES
from hope.api.dashboard_snapshot import build_dashboard_snapshot
from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.fixture(scope='module')
def restricted_database():
    value=os.getenv('HOPE_TEST_DATABASE_ADMIN_URL') or os.getenv('HOPE_DATABASE_URL')
    if not value:pytest.skip('HOPE_DATABASE_URL is not configured')
    url=make_url(value);suffix=uuid4().hex
    database='dashboard_test_'+suffix
    owner='dashboard_owner_'+suffix;reader='dashboard_login_'+suffix
    password=secrets.token_hex(32)
    admin=create_engine(url.set(database='postgres'),isolation_level='AUTOCOMMIT')
    created=[];database_created=False;engine=None
    try:
        with admin.connect() as c:
            assert c.execute(text('SELECT rolsuper FROM pg_roles WHERE rolname=current_user')).scalar_one(), 'Disposable administrator required; never widen the operating login'
            for role in (owner,reader):
                c.execute(text(f'CREATE ROLE "{role}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD \'{password}\''))
                created.append(role)
            c.execute(text(f'CREATE DATABASE "{database}" OWNER "{owner}"'));database_created=True
        owner_url=url.set(username=owner,password=password,database=database)
        reader_url=url.set(username=reader,password=password,database=database)
        engine=create_engine(owner_url)
        with engine.begin() as c:
            assert c.execute(text('SELECT current_user=session_user AND NOT rolsuper AND NOT rolcreaterole AND NOT rolcreatedb FROM pg_roles WHERE rolname=current_user')).scalar_one()
            assert c.execute(text('SELECT datdba=(SELECT oid FROM pg_roles WHERE rolname=current_user) FROM pg_database WHERE datname=current_database()')).scalar_one()
            c.execute(text('CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public'))
        yield dict(engine=engine,admin=admin,owner=owner,reader=reader,owner_url=owner_url,reader_url=reader_url)
    finally:
        if engine is not None:engine.dispose(close=True)
        with admin.connect() as c:
            if database_created:c.execute(text(f'DROP DATABASE "{database}" WITH (FORCE)'))
            for role in reversed(created):c.execute(text(f'DROP ROLE "{role}"'))
        admin.dispose(close=True)


@contextmanager
def isolated_schema(case):
    schema='dashboard_m1_'+uuid4().hex
    with case['engine'].begin() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
        c.execute(text(f'SET LOCAL search_path TO "{schema}",public'))
        apply_migrations(c,Path(__file__).parents[2]/'migrations')
    try:yield schema
    finally:
        with case['engine'].begin() as c:c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def privilege_state(case,schema):
    with case['engine'].connect() as c:
        return tuple(c.execute(text("SELECT c.relname,has_table_privilege(:reader,c.oid,'SELECT') FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema AND c.relkind IN ('r','p','v','m','f') ORDER BY c.relname"),dict(reader=case['reader'],schema=schema)))


@pytest.mark.integration
def test_restricted_owner_and_real_login_preserve_permission_denial_and_cleanup(restricted_database):
    case=restricted_database
    with isolated_schema(case) as schema:
        before=privilege_state(case,schema)
        with restricted_dashboard_reader(case['engine'],schema,reader_url=case['reader_url']) as c:
            assert c.execute(text('SELECT current_user,session_user')).one()==(case['reader'],case['reader'])
            c.rollback()
            with pytest.raises(DBAPIError) as denied:
                with c.begin_nested():c.execute(text(f'SELECT count(*) FROM "{schema}".datasets'))
            assert denied.value.orig.sqlstate=='42501';c.rollback()
            projections=build_dashboard_snapshot(c)['projections']
            assert projections['datasets']=={'status':'UNAVAILABLE'}
            assert projections['strategies']=={'status':'AVAILABLE','versions':0}
            assert projections['decisions']['total']==0
            assert projections['strategy_catalog']==projections['certified_chain']=={'status':'UNAVAILABLE'}
            assert c.execute(text('SHOW transaction_read_only')).scalar_one()=='on'
        assert privilege_state(case,schema)==before
        with case['engine'].connect() as c:
            assert c.execute(text('SELECT rolcanlogin AND NOT rolcreaterole FROM pg_roles WHERE rolname=:reader'),dict(reader=case['reader'])).scalar_one()
            assert not c.execute(text("SELECT has_schema_privilege(:reader,:schema,'USAGE')"),dict(reader=case['reader'],schema=schema)).scalar_one()


@pytest.mark.integration
def test_missing_reader_is_precise_failure_not_skip_or_owner_fallback(restricted_database):
    case=restricted_database
    with isolated_schema(case) as schema:
        with pytest.raises(RuntimeError,match='SEPARATE_LOGIN_REQUIRED'):
            with restricted_dashboard_reader(case['engine'],schema,reader_url=''):pytest.fail('Owner was used as reader')


@pytest.mark.integration
@pytest.mark.parametrize('attribute',['SUPERUSER','CREATEROLE','CREATEDB','REPLICATION','BYPASSRLS'])
def test_privileged_reader_is_refused_before_grants(restricted_database,attribute):
    case=restricted_database
    with isolated_schema(case) as schema:
        with case['admin'].connect() as c:c.execute(text(f'ALTER ROLE "{case["reader"]}" {attribute}'))
        try:
            with pytest.raises(RuntimeError,match='PRIVILEGED_LOGIN_DENIED'):
                with restricted_dashboard_reader(case['engine'],schema,reader_url=case['reader_url']):pytest.fail('Privileged reader admitted')
        finally:
            with case['admin'].connect() as c:c.execute(text(f'ALTER ROLE "{case["reader"]}" NO{attribute}'))


@pytest.mark.integration
def test_same_owner_login_and_different_database_are_not_accepted(restricted_database):
    case=restricted_database
    with isolated_schema(case) as schema:
        for url,code in [(case['owner_url'],'LOGIN_IDENTITY_MISMATCH'),(case['reader_url'].set(database='postgres'),'DATABASE_IDENTITY_MISMATCH')]:
            with pytest.raises(RuntimeError,match=code):
                with restricted_dashboard_reader(case['engine'],schema,reader_url=url):pytest.fail('Identity mismatch admitted')


@pytest.mark.integration
def test_noinherit_membership_in_owner_is_refused(restricted_database):
    case=restricted_database
    with isolated_schema(case) as schema:
        with case['admin'].connect() as c:c.execute(text(f'GRANT "{case["owner"]}" TO "{case["reader"]}"'))
        try:
            with pytest.raises(RuntimeError,match='OWNER_MEMBERSHIP_DENIED'):
                with restricted_dashboard_reader(case['engine'],schema,reader_url=case['reader_url']):pytest.fail('Owner member admitted')
        finally:
            with case['admin'].connect() as c:c.execute(text(f'REVOKE "{case["owner"]}" FROM "{case["reader"]}"'))


@pytest.mark.integration
@pytest.mark.parametrize('permission',['CREATE','SELECT','USAGE','EXISTING_SELECT','INSERT'])
def test_preexisting_access_is_refused_and_not_clobbered(restricted_database,permission):
    case=restricted_database
    with isolated_schema(case) as schema:
        grant=({'CREATE':f'CREATE ON SCHEMA "{schema}"','USAGE':f'USAGE ON SCHEMA "{schema}"',
            'SELECT':f'SELECT ON TABLE "{schema}".datasets','EXISTING_SELECT':f'SELECT ON TABLE "{schema}".strategy_versions',
            'INSERT':f'INSERT ON TABLE "{schema}".strategy_versions'})[permission]
        with case['engine'].begin() as c:c.execute(text(f'GRANT {grant} TO "{case["reader"]}"'))
        before=privilege_state(case,schema)
        try:
            with pytest.raises(RuntimeError,match='SCHEMA_WRITE_DENIED|PREEXISTING_DATASET_ACCESS_DENIED|PREEXISTING_SCHEMA_GRANT_DENIED|PREEXISTING_TABLE_GRANT_DENIED|TABLE_WRITE_DENIED'):
                with restricted_dashboard_reader(case['engine'],schema,reader_url=case['reader_url']):pytest.fail('Preexisting access admitted')
            assert privilege_state(case,schema)==before
            with case['engine'].connect() as c:
                if permission in {'CREATE','USAGE'}:
                    assert c.execute(text('SELECT has_schema_privilege(:reader,:schema,:permission)'),dict(reader=case['reader'],schema=schema,permission=permission)).scalar_one()
                elif permission=='INSERT':
                    assert c.execute(text("SELECT has_table_privilege(:reader,:table,'INSERT')"),dict(reader=case['reader'],table=f'{schema}.strategy_versions')).scalar_one()
        finally:
            with case['engine'].begin() as c:c.execute(text(f'REVOKE {grant} FROM "{case["reader"]}"'))


@pytest.mark.integration
def test_body_failure_still_restores_scoped_privileges(restricted_database):
    case=restricted_database
    with isolated_schema(case) as schema:
        before=privilege_state(case,schema)
        with pytest.raises(ValueError,match='ORIGINAL_BODY_FAILURE'):
            with restricted_dashboard_reader(case['engine'],schema,reader_url=case['reader_url']):raise ValueError('ORIGINAL_BODY_FAILURE')
        assert privilege_state(case,schema)==before


@pytest.mark.integration
def test_concurrent_schemas_keep_separate_sessions_and_cleanup(restricted_database):
    case=restricted_database;barrier=Barrier(2)
    with isolated_schema(case) as first,isolated_schema(case) as second:
        before={s:privilege_state(case,s) for s in (first,second)}
        def read(schema):
            with restricted_dashboard_reader(case['engine'],schema,reader_url=case['reader_url']) as c:
                barrier.wait(timeout=10)
                assert c.execute(text('SELECT current_user,session_user,current_schema()')).one()==(case['reader'],case['reader'],schema)
                c.rollback()
                assert build_dashboard_snapshot(c)['projections']['datasets']=={'status':'UNAVAILABLE'}
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(read,(first,second)))
        assert all(privilege_state(case,s)==before[s] for s in (first,second))


@pytest.mark.integration
def test_original_projection_assertions_run_as_nocreaterole_owner(restricted_database,monkeypatch):
    from test_dashboard_m1_authenticated_projections_pg import test_missing_optional_select_permission_is_unavailable_without_poisoning_other_sections as original
    case=restricted_database
    monkeypatch.setenv('HOPE_TEST_DASHBOARD_READER_URL',case['reader_url'].render_as_string(hide_password=False))
    with isolated_schema(case) as schema: original(case['engine'],schema)


@pytest.mark.integration
def test_admin_auto_provisioning_uses_real_login_and_removes_only_its_role(restricted_database):
    case=restricted_database
    admin_db=create_engine(case['admin'].url.set(database=case['engine'].url.database))
    try:
        with isolated_schema(dict(engine=admin_db)) as schema:
            with restricted_dashboard_reader(admin_db,schema,reader_url='') as c:
                role,session=c.execute(text('SELECT current_user,session_user')).one()
                assert role==session and role.startswith('dashboard_reader_')
            with admin_db.connect() as c:
                assert c.execute(text('SELECT count(*) FROM pg_roles WHERE rolname=:role'),dict(role=role)).scalar_one()==0
                assert c.execute(text('SELECT count(*) FROM pg_roles WHERE rolname=:role'),dict(role=case['reader'])).scalar_one()==1
    finally:admin_db.dispose(close=True)


@pytest.mark.integration
def test_reader_that_owns_a_table_is_refused(restricted_database):
    case=restricted_database
    admin_db=create_engine(case['admin'].url.set(database=case['engine'].url.database),isolation_level='AUTOCOMMIT')
    try:
        with isolated_schema(case) as schema:
            with admin_db.connect() as c:
                c.execute(text(f'GRANT CREATE ON SCHEMA "{schema}" TO "{case["reader"]}"'))
                c.execute(text(f'ALTER TABLE "{schema}".datasets OWNER TO "{case["reader"]}"'))
                c.execute(text(f'REVOKE CREATE ON SCHEMA "{schema}" FROM "{case["reader"]}"'))
            try:
                with pytest.raises(RuntimeError,match='TABLE_OWNER_DENIED'):
                    with restricted_dashboard_reader(case['engine'],schema,reader_url=case['reader_url']):pytest.fail('Table owner admitted')
            finally:
                with admin_db.connect() as c:c.execute(text(f'ALTER TABLE "{schema}".datasets OWNER TO "{case["owner"]}"'))
    finally:admin_db.dispose(close=True)


@pytest.mark.integration
@pytest.mark.parametrize('body_fails',[True,False])
def test_actual_cleanup_backend_loss_preserves_primary_or_fails_closed(restricted_database,body_fails):
    from sqlalchemy import event
    case=restricted_database;killed=[]
    def terminate(connection,cursor,statement,parameters,context,executemany):
        if statement.startswith('REVOKE SELECT ON TABLE') and not killed:
            pid=connection.execute(text('SELECT pg_backend_pid()')).scalar_one()
            with case['admin'].connect() as killer:
                assert killer.execute(text('SELECT pg_terminate_backend(:pid)'),dict(pid=pid)).scalar_one()
            killed.append(pid)
    with isolated_schema(case) as schema:
        event.listen(case['engine'],'before_cursor_execute',terminate)
        try:
            expected=ValueError if body_fails else DBAPIError
            with pytest.raises(expected) as caught:
                with restricted_dashboard_reader(case['engine'],schema,reader_url=case['reader_url']):
                    if body_fails:raise ValueError('PRIMARY_BODY_FAILURE')
            assert len(killed)==1
            if body_fails:
                assert str(caught.value)=='PRIMARY_BODY_FAILURE'
                assert any('cleanup failed' in note for note in caught.value.__notes__)
        finally:event.remove(case['engine'],'before_cursor_execute',terminate)
