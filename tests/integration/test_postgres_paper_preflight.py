"""Disposable schema only: real PostgreSQL read-only preflight, never production."""
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError

from hope.api import paper_preflight as p

pytestmark=pytest.mark.integration


@pytest.fixture
def native_records(tmp_path,monkeypatch):
    url=os.getenv('HOPE_DATABASE_URL')
    if not url or os.name!='posix':pytest.skip('Disposable PostgreSQL 16/17 Linux CI required')
    monkeypatch.setattr(p.runtime,'OWNER',os.geteuid())
    directory=tmp_path/'migrations';directory.mkdir()
    (directory/'001_first.sql').write_bytes(b'SELECT 1;\r\n')
    (directory/'002_second.sql').write_bytes(b'SELECT 2;\n')
    expected,_=p.inventory(directory)
    engine=create_engine(url)
    schema='hope_preflight_'+uuid4().hex
    try:
        # The CI-provided database is disposable. All setup/teardown writes are
        # restricted to this test's unique schema; public/financial tables stay
        # untouched by these acceptance tests and by the observer itself.
        with engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            connection.exec_driver_sql(f'CREATE TABLE "{schema}".hope_schema_migrations(version TEXT PRIMARY KEY,checksum CHAR(64))')
            for version,checksum in expected.items():
                connection.execute(text(f'INSERT INTO "{schema}".hope_schema_migrations VALUES (:version,:checksum)'),
                                   dict(version=version,checksum=checksum))
        yield engine,schema,directory
    finally:
        with engine.begin() as connection:connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        engine.dispose()


@pytest.mark.parametrize('fault',[None,'pending','unknown','mismatch','unsigned','missing','readwrite','unbounded'])
def test_real_readonly_migration_probe_never_writes_and_refuses_unsafe_history(native_records,fault):
    engine,schema,directory=native_records
    with engine.begin() as connection:
        if fault=='pending':connection.exec_driver_sql(f'DELETE FROM "{schema}".hope_schema_migrations WHERE version=\'002_second.sql\'')
        elif fault=='unknown':connection.exec_driver_sql(f'INSERT INTO "{schema}".hope_schema_migrations VALUES (\'003_unknown.sql\',repeat(\'a\',64))')
        elif fault=='mismatch':connection.exec_driver_sql(f'UPDATE "{schema}".hope_schema_migrations SET checksum=repeat(\'f\',64)')
        elif fault=='unsigned':connection.exec_driver_sql(f'UPDATE "{schema}".hope_schema_migrations SET checksum=NULL')
        elif fault=='missing':connection.exec_driver_sql(f'DROP TABLE "{schema}".hope_schema_migrations')
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        if fault!='readwrite':connection.exec_driver_sql('SET TRANSACTION READ ONLY')
        connection.exec_driver_sql("SET LOCAL statement_timeout='5s'")
        connection.exec_driver_sql("SET LOCAL lock_timeout='0'" if fault=='unbounded' else "SET LOCAL lock_timeout='1s'")
        queries=[]
        def capture(conn,cursor,statement,parameters,context,executemany):queries.append(statement)
        event.listen(connection,'before_cursor_execute',capture)
        try:result=p.migration_compatibility(connection,directory,schema=schema)
        finally:event.remove(connection,'before_cursor_execute',capture)
        assert result['state']==('VERIFIED' if fault is None else 'UNVERIFIED' if fault=='unsigned' else 'BLOCKED')
        assert queries and all(statement.startswith(('SELECT ','SHOW ')) for statement in queries)
        assert connection.execute(text('SHOW transaction_read_only')).scalar_one()==('off' if fault=='readwrite' else 'on')
        if fault not in {'readwrite','missing'}:
            with pytest.raises(DBAPIError,match='read-only transaction'):
                with connection.begin_nested():
                    connection.exec_driver_sql(f'INSERT INTO "{schema}".hope_schema_migrations VALUES (\'999_forbidden.sql\',NULL)')
        connection.rollback()
    if fault!='missing':
        with engine.connect() as connection:
            assert connection.execute(text(f'SELECT count(*) FROM "{schema}".hope_schema_migrations WHERE version=\'999_forbidden.sql\'')).scalar_one()==0


def test_real_readonly_record_probe_does_not_create_missing_schema(native_records):
    engine,schema,directory=native_records
    missing='hope_preflight_missing_'+uuid4().hex
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        connection.exec_driver_sql('SET TRANSACTION READ ONLY')
        connection.exec_driver_sql("SET LOCAL statement_timeout='5s'")
        connection.exec_driver_sql("SET LOCAL lock_timeout='1s'")
        result=p.migration_compatibility(connection,directory,schema=missing)
        assert result['state']=='BLOCKED' and result['code']=='MIGRATION_RECORDS_REQUIRED'
        assert connection.execute(text('SELECT to_regnamespace(:schema)'),{'schema':missing}).scalar_one() is None
