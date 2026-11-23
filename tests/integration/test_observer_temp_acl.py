"""Real disposable PG16/17 rehearsal of review-only TEMP ACL proposals."""
from dataclasses import replace
import json
from uuid import uuid4
import pytest
from psycopg.errors import RaiseException
from sqlalchemy import create_engine,text
from sqlalchemy.exc import DBAPIError
from test_dashboard_reader_boundary import restricted_database
from hope.api.observer_acl_plan import capture_temp_boundary,propose_temp_restriction
from hope.security.dashboard_observer import ObserverProfile,verify_observer
from hope.infrastructure.repositories.paper_handoff import database_identity

pytestmark=pytest.mark.integration

@pytest.fixture
def acl_case(restricted_database):
    case=restricted_database;name='temp_app_'+uuid4().hex
    admin=create_engine(case['admin'].url.set(database=case['reader_url'].database))
    password=case['reader_url'].password
    with admin.begin() as c:c.exec_driver_sql('CREATE ROLE "'+name+'" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD \''+password+"'")
    app=create_engine(case['reader_url'].set(username=name));reader=create_engine(case['reader_url'])
    def capture():
        with case['engine'].connect() as c:return capture_temp_boundary(c,case['reader'])
    def execute(sql,engine=None):
        pooled=(case['engine'] if engine is None else engine).raw_connection()
        connection=pooled.driver_connection;previous=connection.autocommit
        try:
            connection.autocommit=True
            with connection.cursor() as cursor:cursor.execute(sql)
        finally:
            connection.rollback();connection.autocommit=previous;pooled.close()
    before=capture()
    dependencies=tuple(r['name'] for r in before['roles'] if r['login'] and r['name']!=case['reader'])
    plan=propose_temp_restriction(before,application_roles=dependencies)
    try:yield dict(base=case,admin=admin,reader=reader,app=app,app_name=name,capture=capture,execute=execute,before=before,plan=plan)
    finally:
        current=capture()
        if current==json.loads(plan.after_json):execute(plan.sql(rollback=True))
        app.dispose();reader.dispose()
        with admin.begin() as c:
            c.exec_driver_sql('DROP OWNED BY "'+name+'"')
            c.exec_driver_sql('DROP ROLE "'+name+'"')
        admin.dispose()


def test_real_application_temp_is_preserved_observer_denied_and_exact_rollback(acl_case):
    c=acl_case;p=c['plan'];before=c['before']
    assert c['app_name'] in p.preserved_roles
    with c['reader'].connect() as r:
        assert capture_temp_boundary(r,c['base']['reader'])==before
    with c['admin'].connect() as r:
        other_before=r.exec_driver_sql('SELECT oid,datacl::text FROM pg_catalog.pg_database WHERE oid<>'+str(before['database']['oid'])+' ORDER BY oid').all()
    c['execute'](p.sql())
    assert c['capture']()==json.loads(p.after_json)
    with c['admin'].connect() as r:
        assert r.exec_driver_sql('SELECT oid,datacl::text FROM pg_catalog.pg_database WHERE oid<>'+str(before['database']['oid'])+' ORDER BY oid').all()==other_before
    with c['reader'].begin() as r:
        with pytest.raises(DBAPIError) as error:r.exec_driver_sql('CREATE TEMP TABLE denied_temp(id int)')
    assert error.value.orig.sqlstate=='42501'
    with c['app'].begin() as r:
        r.exec_driver_sql('CREATE TEMP TABLE retained_temp(id int) ON COMMIT DROP')
        r.exec_driver_sql('INSERT INTO retained_temp VALUES(1)')
        assert r.exec_driver_sql('SELECT sum(id) FROM retained_temp').scalar_one()==1
    with c['base']['engine'].connect() as r:identity=database_identity(r)
    with c['reader'].connect() as r:
        r.exec_driver_sql('SET TRANSACTION READ ONLY')
        assert verify_observer(r,ObserverProfile(c['base']['reader'],identity))==identity
    c['execute'](p.sql(rollback=True))
    assert c['capture']()==before


def test_unreviewed_application_login_prevents_proposal(acl_case):
    with pytest.raises(ValueError,match='UNREVIEWED_LOGIN_DEPENDENCY'):
        propose_temp_restriction(acl_case['before'],application_roles=())


@pytest.mark.parametrize('drift',['acl','role','membership'])
def test_stale_inventory_cannot_revoke_or_grant_any_privilege(acl_case,drift):
    c=acl_case;role=c['base']['reader'];app=c['app_name'];db=c['base']['reader_url'].database
    setup={'acl':'GRANT CONNECT ON DATABASE "'+db+'" TO "'+role+'"',
        'role':'ALTER ROLE "'+app+'" NOLOGIN',
        'membership':'GRANT "'+app+'" TO "'+role+'" WITH INHERIT FALSE, SET TRUE'}[drift]
    cleanup={'acl':'REVOKE CONNECT ON DATABASE "'+db+'" FROM "'+role+'"',
        'role':'ALTER ROLE "'+app+'" LOGIN',
        'membership':'REVOKE "'+app+'" FROM "'+role+'"'}[drift]
    try:
        with c['admin'].begin() as r:r.exec_driver_sql(setup)
        changed=c['capture']()
        with pytest.raises(RaiseException,match='OBSERVER_TEMP_BASELINE_CHANGED'):c['execute'](c['plan'].sql())
        assert c['capture']()==changed
    finally:
        with c['admin'].begin() as r:r.exec_driver_sql(cleanup)
    assert c['capture']()==c['before']


def test_replay_and_wrong_actor_are_denied_without_acl_effects(acl_case):
    c=acl_case;p=c['plan']
    with pytest.raises(RaiseException,match='OWNER_LOGIN_REQUIRED'):c['execute'](p.sql(),c['reader'])
    assert c['capture']()==c['before']
    c['execute'](p.sql());after=c['capture']()
    with pytest.raises(RaiseException,match='BASELINE_CHANGED'):c['execute'](p.sql())
    assert c['capture']()==after
    c['execute'](p.sql(rollback=True))
    with pytest.raises(RaiseException,match='BASELINE_CHANGED'):c['execute'](p.sql(rollback=True))
    assert c['capture']()==c['before']


def test_failed_postcondition_rolls_back_all_acl_changes(acl_case):
    c=acl_case;p=c['plan'];after=json.loads(p.after_json);after['database']['oid']+=1
    corrupt=replace(p,after_json=json.dumps(after))
    with pytest.raises(RaiseException,match='POSTCONDITION_FAILED'):c['execute'](corrupt.sql())
    assert c['capture']()==c['before']


def test_rollback_preserves_concurrent_external_acl_change(acl_case):
    c=acl_case;p=c['plan'];role=c['base']['reader'];db=c['base']['reader_url'].database
    c['execute'](p.sql())
    try:
        with c['admin'].begin() as r:r.exec_driver_sql('GRANT CONNECT ON DATABASE "'+db+'" TO "'+role+'"')
        changed=c['capture']()
        with pytest.raises(RaiseException,match='BASELINE_CHANGED'):c['execute'](p.sql(rollback=True))
        assert c['capture']()==changed
    finally:
        with c['admin'].begin() as r:r.exec_driver_sql('REVOKE CONNECT ON DATABASE "'+db+'" FROM "'+role+'"')
    c['execute'](p.sql(rollback=True));assert c['capture']()==c['before']


def test_observer_membership_in_dependency_is_not_papered_over(acl_case):
    c=acl_case;role=c['base']['reader'];app=c['app_name']
    try:
        with c['admin'].begin() as r:r.exec_driver_sql('GRANT "'+app+'" TO "'+role+'" WITH INHERIT FALSE, SET TRUE')
        value=c['capture']();dependencies=tuple(r['name'] for r in value['roles'] if r['login'] and r['name']!=role)
        with pytest.raises(ValueError,match='DEPENDENCY_OVERLAP'):propose_temp_restriction(value,application_roles=dependencies)
    finally:
        with c['admin'].begin() as r:r.exec_driver_sql('REVOKE "'+app+'" FROM "'+role+'"')


def test_existing_transaction_refuses_before_inventory_query(acl_case):
    with acl_case['reader'].connect() as c:
        c.exec_driver_sql('SELECT 1')
        with pytest.raises(ValueError,match='FRESH_TRANSACTION_REQUIRED'):capture_temp_boundary(c,acl_case['base']['reader'])
