"""Destructive tests confined to generated disposable databases on localhost CI."""
import os
from pathlib import Path
import re
import shutil
from uuid import uuid4

import pytest
from sqlalchemy import create_engine,text,event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from hope.infrastructure.postgres.migrations import apply_migrations,migration_inventory,MigrationError
from hope.api.paper_release_plan import upgrade_plan
from hope.api import runtime_integrity as r

pytestmark=pytest.mark.integration
MIGRATIONS=Path(__file__).parents[2]/'migrations'


@pytest.fixture
def isolated_database():
    raw=os.getenv('HOPE_DATABASE_URL');major=os.getenv('HOPE_TEST_POSTGRES_MAJOR')
    if not raw or major not in {'16','17'}:pytest.skip('Explicit disposable PostgreSQL matrix required')
    url=make_url(raw)
    assert url.host in {'localhost','127.0.0.1'} and url.database=='hope', 'Disposable localhost CI only'
    name='hope_rehearsal_'+uuid4().hex
    assert re.fullmatch('hope_rehearsal_[0-9a-f]{32}',name)
    admin=create_engine(url,isolation_level='AUTOCOMMIT')
    with admin.connect() as connection:connection.exec_driver_sql('CREATE DATABASE '+name)
    engine=create_engine(url.set(database=name),connect_args={'options':'-c statement_timeout=15000 -c lock_timeout=1000'})
    try:
        with engine.connect() as connection:
            assert int(connection.execute(text('SHOW server_version_num')).scalar_one())//10000==int(major)
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as connection:connection.exec_driver_sql('DROP DATABASE '+name+' WITH (FORCE)')
        admin.dispose()


def prepare_eight(engine,tmp_path):
    old=tmp_path/'old';old.mkdir()
    for item in sorted(MIGRATIONS.glob('*.sql'))[:8]:shutil.copyfile(item,old/item.name)
    with engine.begin() as connection:
        assert len(apply_migrations(connection,old))==8
        connection.execute(text("INSERT INTO instruments(instrument_id,canonical_symbol,exchange,status) VALUES (:id,'SYNTHETIC_REHEARSAL','TEST','ACTIVE')"),{'id':uuid4()})
    return old


@pytest.mark.parametrize('initial',[0,8])
def test_full_chain_fresh_and_008_upgrade_preserve_data_and_verify_postconditions(isolated_database,tmp_path,monkeypatch,initial):
    engine=isolated_database
    if initial:prepare_eight(engine,tmp_path)
    with engine.begin() as connection:
        changed=apply_migrations(connection,MIGRATIONS)
        assert len(changed)==len(migration_inventory(MIGRATIONS))-initial
        assert apply_migrations(connection,MIGRATIONS)==[]
        assert connection.execute(text('SELECT count(*) FROM public.hope_schema_migrations')).scalar_one()==234
        assert connection.execute(text("SELECT state FROM public.paper_environment_control_events ORDER BY control_sequence DESC LIMIT 1")).scalar_one()=='RUNNING'
        assert connection.execute(text("SELECT count(*) FROM pg_catalog.pg_trigger WHERE tgname IN ('trg_paper_handoff_intent_insert','trg_paper_handoff_outcome_insert') AND tgenabled='O'")).scalar_one()==2
        assert connection.execute(text('SELECT count(*) FROM paper_effects')).scalar_one()==0
        if initial:assert connection.execute(text("SELECT count(*) FROM instruments WHERE canonical_symbol='SYNTHETIC_REHEARSAL'")).scalar_one()==1
    monkeypatch.setattr(r,'OWNER',os.geteuid())
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        connection.exec_driver_sql('SET TRANSACTION READ ONLY')
        observed=upgrade_plan(connection,MIGRATIONS)
        assert observed['state']=='VERIFIED' and observed['prefix_verified'] is True and observed['pending']==[]
        assert observed['deployment_go'] is observed['first_cycle_go'] is False


def test_eight_to_current_plan_is_exact_and_never_grants_go(isolated_database,tmp_path,monkeypatch):
    engine=isolated_database;prepare_eight(engine,tmp_path);monkeypatch.setattr(r,'OWNER',os.geteuid())
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        connection.exec_driver_sql('SET TRANSACTION READ ONLY')
        observed=upgrade_plan(connection,MIGRATIONS)
        assert observed['prefix_verified'] is True and observed['state']=='BLOCKED'
        assert observed['applied']==8 and len(observed['pending'])==226
        assert observed['pending'][0]['name']=='009_job_run_identity.sql'
        assert observed['pending'][-1]['name']=='234_paper_handoff_intents.sql'
        assert connection.execute(text('SELECT count(*) FROM public.hope_schema_migrations')).scalar_one()==8


@pytest.mark.parametrize('fault',['hole','unsigned','tampered','unknown'])
def test_invalid_008_history_is_not_repaired_or_partially_upgraded(isolated_database,tmp_path,fault):
    engine=isolated_database;prepare_eight(engine,tmp_path)
    with engine.begin() as connection:
        if fault=='hole':connection.exec_driver_sql("DELETE FROM hope_schema_migrations WHERE version='007_identity_namespace_integrity.sql'")
        elif fault=='unsigned':connection.exec_driver_sql("UPDATE hope_schema_migrations SET checksum=NULL WHERE version='001_initial.sql'")
        elif fault=='tampered':connection.exec_driver_sql("UPDATE hope_schema_migrations SET checksum=repeat('a',64) WHERE version='008_migration_checksums.sql'")
        else:connection.exec_driver_sql("INSERT INTO hope_schema_migrations(version,checksum) VALUES ('999_unknown.sql',repeat('a',64))")
    with engine.begin() as connection:
        before=connection.execute(text('SELECT version,checksum FROM hope_schema_migrations ORDER BY version')).all()
        with pytest.raises(MigrationError):apply_migrations(connection,MIGRATIONS)
        assert connection.execute(text("SELECT to_regclass('public.job_runs')")).scalar_one() is None
        assert connection.execute(text('SELECT version,checksum FROM hope_schema_migrations ORDER BY version')).all()==before


@pytest.mark.parametrize('fault',['late_sql','source_change'])
def test_interrupted_upgrade_rolls_back_even_if_caller_catches_and_commits(isolated_database,tmp_path,fault):
    engine=isolated_database;prepare_eight(engine,tmp_path)
    chain=tmp_path/'chain';shutil.copytree(MIGRATIONS,chain)
    if fault=='late_sql':(chain/'234_paper_handoff_intents.sql').write_text('SELECT * FROM synthetic_missing_relation;')
    else:
        @event.listens_for(engine,'after_cursor_execute')
        def change_source(connection,cursor,statement,parameters,context,executemany):
            if statement.startswith('-- Durable idempotency key'):(chain/'001_initial.sql').write_text('SELECT 77;')
    with engine.begin() as connection:
        with pytest.raises((DBAPIError,MigrationError)):apply_migrations(connection,chain)
        assert connection.execute(text('SELECT count(*) FROM hope_schema_migrations')).scalar_one()==8
        assert connection.execute(text("SELECT to_regclass('public.job_runs')")).scalar_one() is None
    with engine.begin() as connection:assert len(apply_migrations(connection,MIGRATIONS))==226


def test_concurrent_runner_refuses_then_can_retry_from_committed_prefix(isolated_database,tmp_path):
    engine=isolated_database;old=tmp_path/'old';old.mkdir()
    for item in sorted(MIGRATIONS.glob('*.sql'))[:8]:shutil.copyfile(item,old/item.name)
    with engine.begin() as first:
        apply_migrations(first,old)
        with engine.begin() as contender:
            with pytest.raises(MigrationError,match='concurrent'):apply_migrations(contender,MIGRATIONS)
    with engine.begin() as connection:assert len(apply_migrations(connection,MIGRATIONS))==226


def test_repeatable_read_runner_is_refused_without_ddl(isolated_database):
    with isolated_database.connect().execution_options(isolation_level='REPEATABLE READ') as connection,connection.begin():
        with pytest.raises(MigrationError,match='READ COMMITTED'):apply_migrations(connection,MIGRATIONS)
        assert connection.execute(text("SELECT to_regclass('public.hope_schema_migrations')")).scalar_one() is None


def test_upgraded_catalog_matches_fresh_install_and_detects_disabled_guard(isolated_database,tmp_path):
    from hope.infrastructure.postgres.schema_verification import schema_observation
    engine=isolated_database
    def observe(expected=None):
        with engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
            connection.exec_driver_sql('SET TRANSACTION READ ONLY')
            return schema_observation(connection,expected)
    with engine.begin() as connection:apply_migrations(connection,MIGRATIONS)
    fresh=observe();assert fresh['state']=='UNVERIFIED' and fresh['sha256'] and fresh['objects']>100
    # This database was uniquely generated by this fixture, never the shared CI DB.
    with engine.begin() as connection:
        connection.exec_driver_sql('DROP SCHEMA public CASCADE');connection.exec_driver_sql('CREATE SCHEMA public')
    prepare_eight(engine,tmp_path)
    with engine.begin() as connection:apply_migrations(connection,MIGRATIONS)
    assert observe(fresh['sha256'])['state']=='VERIFIED'
    with engine.begin() as connection:connection.exec_driver_sql('ALTER TABLE public.paper_handoff_intents DISABLE TRIGGER trg_paper_handoff_intent_insert')
    assert observe(fresh['sha256'])['state']=='MISMATCH'
