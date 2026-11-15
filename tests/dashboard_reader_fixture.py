"""TEST ONLY: genuine restricted LOGIN for isolated dashboard permission tests.

An optional separately provisioned reader supports NOCREATEROLE database owners.
Nothing here provisions production roles or weakens application admission.
"""
from contextlib import contextmanager
import os
import re
import secrets
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

TABLES = ('research_runs','job_runs','paper_environment_control_events','paper_effects','audit_events',
    'strategy_versions','experiments','research_decisions','paper_no_trade_decisions','market_bars')


def require(value, code):
    if not value: raise RuntimeError('DASHBOARD_TEST_READER_' + code)


@contextmanager
def restricted_dashboard_reader(owner_engine, schema, *, reader_url=None):
    require(type(schema) is str and re.fullmatch(r'dashboard_m1_[0-9a-f]{32}',schema), 'ISOLATED_SCHEMA_REQUIRED')
    configured = reader_url if reader_url is not None else os.getenv('HOPE_TEST_DASHBOARD_READER_URL')
    with owner_engine.connect() as owner:
        owner_name,session_name,superuser,createrole=owner.execute(text(
            'SELECT current_user,session_user,rolsuper,rolcreaterole FROM pg_roles WHERE rolname=current_user')).one()
        require(owner_name==session_name,'OWNER_SESSION_ROLE_MISMATCH')
        require(owner.execute(text('SELECT nspowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) '
            'FROM pg_namespace WHERE nspname=:schema'),dict(schema=schema)).scalar_one(),'SCHEMA_OWNER_REQUIRED')
    created = not configured
    if created:
        require(superuser or createrole,'SEPARATE_LOGIN_REQUIRED')
        role='dashboard_reader_'+uuid4().hex
        password=secrets.token_hex(32)
        with owner_engine.begin() as owner:
            owner.execute(text(f'CREATE ROLE "{role}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
                f'NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD \'{password}\''))
        url=owner_engine.url.set(username=role,password=password)
    else:
        url=make_url(configured)
        require(url.get_backend_name()=='postgresql' and owner_engine.url.get_backend_name()=='postgresql'
            and (url.host,url.port,url.database)==(owner_engine.url.host,owner_engine.url.port,owner_engine.url.database),
            'DATABASE_IDENTITY_MISMATCH')
        role=url.username
        require(type(role) is str and re.fullmatch(r'[a-z_][a-z0-9_]{0,62}',role),'LOGIN_NAME_INVALID')
    reader_engine=None
    granted=False
    primary=None
    try:
        reader_engine=create_engine(url,hide_parameters=True,
            connect_args={'connect_timeout':5,'options':'-c statement_timeout=5000 -c lock_timeout=2000'})
        with reader_engine.connect() as reader:
            identity=reader.execute(text('SELECT current_user,session_user,rolsuper,rolcreaterole,rolcreatedb,'
                'rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user')).one()
            require(identity[0]==identity[1]==role and role!=owner_name,'LOGIN_IDENTITY_MISMATCH')
            require(not any(identity[2:]),'PRIVILEGED_LOGIN_DENIED')
            require(not reader.execute(text("SELECT pg_has_role(current_user,:owner,'MEMBER')"),
                dict(owner=owner_name)).scalar_one(),'OWNER_MEMBERSHIP_DENIED')
            require(not reader.execute(text("SELECT has_schema_privilege(current_user,:schema,'CREATE')"),
                dict(schema=schema)).scalar_one(),'SCHEMA_WRITE_DENIED')
            require(not reader.execute(text("SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n "
                "ON n.oid=c.relnamespace WHERE n.nspname=:schema AND c.relkind IN ('r','p','v','m','f') "
                "AND c.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) "
                ")"),dict(schema=schema)).scalar_one(),
                'TABLE_OWNER_DENIED')
            require(not reader.execute(text("SELECT has_table_privilege(current_user,c.oid,'SELECT') FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema AND c.relname='datasets'"),
                dict(schema=schema)).scalar_one(),'PREEXISTING_DATASET_ACCESS_DENIED')
            require(not reader.execute(text("SELECT EXISTS(SELECT 1 FROM pg_namespace n CROSS JOIN LATERAL aclexplode(n.nspacl) a WHERE n.nspname=:schema AND a.grantee=(SELECT oid FROM pg_roles WHERE rolname=current_user) AND a.privilege_type='USAGE')"),dict(schema=schema)).scalar_one(),
                'PREEXISTING_SCHEMA_GRANT_DENIED')
            require(not reader.execute(text("SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace CROSS JOIN LATERAL aclexplode(c.relacl) a WHERE n.nspname=:schema AND a.grantee=(SELECT oid FROM pg_roles WHERE rolname=current_user) AND a.privilege_type='SELECT')"),dict(schema=schema)).scalar_one(),
                'PREEXISTING_TABLE_GRANT_DENIED')
            require(not reader.execute(text("SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema AND c.relkind IN ('r','p','v','m','f') AND has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'))"),dict(schema=schema)).scalar_one(),
                'TABLE_WRITE_DENIED')
        names=','.join(f'"{schema}"."{name}"' for name in TABLES)
        with owner_engine.begin() as owner:
            owner.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"'))
            owner.execute(text(f'GRANT SELECT ON TABLE {names} TO "{role}"'))
        granted=True
        with reader_engine.connect() as reader:
            reader.execute(text(f'SET search_path TO "{schema}",public'))
            reader.commit()
            # No SET ROLE: current_user/session_user must be the real reader.
            yield reader
            reader.rollback()
    except BaseException as exc:
        primary=exc
        raise
    finally:
        if reader_engine is not None: reader_engine.dispose(close=True)
        try:
            if granted:
                with owner_engine.begin() as owner:
                    owner.execute(text(f'REVOKE SELECT ON TABLE {names} FROM "{role}"'))
                    owner.execute(text(f'REVOKE USAGE ON SCHEMA "{schema}" FROM "{role}"'))
            if created:
                with owner_engine.begin() as owner: owner.execute(text(f'DROP ROLE "{role}"'))
        except Exception as cleanup:
            if primary is None: raise
            primary.add_note('Dashboard test reader cleanup failed: '+type(cleanup).__name__)
