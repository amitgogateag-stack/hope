from pathlib import Path
import os
from unittest.mock import Mock
import pytest
from sqlalchemy import create_engine,text,event
from hope.infrastructure.postgres.migrations import apply_migrations,migration_inventory,validate_history,MigrationError


@pytest.fixture
def chain(tmp_path):
    for n in range(1,4):
        (tmp_path/f'{n:03}_step.sql').write_text(f'CREATE TABLE step{n}(id INTEGER);')
    return tmp_path


@pytest.mark.parametrize('fault',['gap','duplicate_number','noncanonical','empty','encoding','oversize',pytest.param('symlink',marks=pytest.mark.skipif(os.name!='posix',reason='Native no-follow link case runs on Linux CI'))])
def test_inventory_refuses_unreviewable_chains(chain,fault):
    if fault=='gap':(chain/'002_step.sql').unlink()
    elif fault=='duplicate_number':(chain/'001_other.sql').write_text('SELECT 1;')
    elif fault=='noncanonical':(chain/'004_BAD.sql').write_text('SELECT 1;')
    elif fault=='empty':(chain/'003_step.sql').write_text(' ')
    elif fault=='encoding':(chain/'003_step.sql').write_bytes(b'\xff')
    elif fault=='oversize':(chain/'003_step.sql').write_bytes(b'x'*(1024*1024+1))
    else:
        (chain/'003_step.sql').unlink();(chain/'003_step.sql').symlink_to(chain/'001_step.sql')
    with pytest.raises(MigrationError):migration_inventory(chain)


@pytest.mark.parametrize('fault',['hole','unknown','duplicate','unsigned','tampered','overflow'])
def test_complete_history_is_checked_before_any_pending_sql(chain,fault):
    inventory=migration_inventory(chain);rows=[(v.name,v.checksum) for v in inventory[:2]]
    if fault=='hole':rows.pop(0)
    elif fault=='unknown':rows.append(('004_unknown.sql','a'*64))
    elif fault=='duplicate':rows.append(rows[0])
    elif fault=='unsigned':rows[1]=(rows[1][0],None)
    elif fault=='tampered':rows[1]=(rows[1][0],'a'*64)
    else:rows=rows*500
    with pytest.raises(MigrationError):validate_history(inventory,rows)


def test_invalid_late_checksum_cannot_apply_earlier_missing_sql(chain):
    engine=create_engine('sqlite://')
    with engine.begin() as connection:
        apply_migrations(connection,chain)
        connection.execute(text("DELETE FROM hope_schema_migrations WHERE version='002_step.sql'"))
        connection.execute(text('DROP TABLE step2'))
        connection.execute(text("UPDATE hope_schema_migrations SET checksum='invalid' WHERE version='003_step.sql'"))
        with pytest.raises(MigrationError):apply_migrations(connection,chain)
        assert connection.execute(text("SELECT name FROM sqlite_master WHERE name='step2'")).first() is None
    engine.dispose()


def test_failed_batch_cannot_be_committed_by_a_catching_caller(chain):
    engine=create_engine('sqlite://')
    (chain/'003_step.sql').write_text('SELECT * FROM missing_table;')
    with engine.begin() as connection:
        with pytest.raises(Exception):apply_migrations(connection,chain)
        assert connection.execute(text("SELECT name FROM sqlite_master WHERE name IN ('step1','step2','hope_schema_migrations')")).all()==[]
    engine.dispose()


def test_source_replacement_after_sql_rolls_back_the_whole_batch(chain):
    engine=create_engine('sqlite://');changed=[]
    @event.listens_for(engine,'after_cursor_execute')
    def alter_source(conn,cursor,statement,parameters,context,executemany):
        if statement.startswith('CREATE TABLE step2'):
            (chain/'001_step.sql').write_text('SELECT 77;');changed.append(True)
    with engine.begin() as connection:
        with pytest.raises(MigrationError,match='source changed'):apply_migrations(connection,chain)
        assert connection.execute(text("SELECT name FROM sqlite_master WHERE name='step1'")).first() is None
    assert changed;engine.dispose()


def test_newline_normalization_preserves_existing_checksums(chain):
    before=migration_inventory(chain)
    for path in chain.glob('*.sql'):path.write_bytes(path.read_bytes().replace(b';',b';\r\n'))
    after=migration_inventory(chain)
    # Added newlines really change source; converting the same newlines does not.
    assert before!=after
    for path in chain.glob('*.sql'):path.write_bytes(path.read_bytes().replace(b'\r\n',b'\n'))
    assert migration_inventory(chain)==after


@pytest.mark.parametrize('major,isolation,lock',[(15,'read committed',True),(16,'serializable',True),(17,'read committed',False)])
def test_postgres_version_snapshot_and_concurrent_runner_fail_before_ddl(chain,major,isolation,lock):
    from unittest.mock import MagicMock
    connection=MagicMock();connection.dialect.name='postgresql'
    connection.execute.side_effect=lambda statement:Mock(scalar_one=lambda:major*10000 if str(statement)=='SHOW server_version_num' else isolation if str(statement)=='SHOW transaction_isolation' else lock)
    with pytest.raises(MigrationError):apply_migrations(connection,chain)
    connection.exec_driver_sql.assert_not_called()
