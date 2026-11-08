from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import re
from sqlalchemy import Connection, text


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    name: str
    sql: str
    checksum: str


def migration_inventory(directory: str | Path) -> tuple[Migration, ...]:
    """Bounded canonical complete chain; normalize newlines like the old runner."""
    directory = Path(directory)
    if not directory.is_dir() or directory.is_symlink():
        raise MigrationError('migration directory unavailable')
    files = sorted(directory.glob('*.sql'))
    if not 0 < len(files) <= 999:
        raise MigrationError('no migrations found; applied migrations may be missing from the migration directory, or inventory limit exceeded')
    result = []
    total = 0
    for number, path in enumerate(files, 1):
        if (not re.fullmatch(r'[0-9]{3}_[a-z0-9_]+\.sql', path.name)
                or int(path.name[:3]) != number or path.is_symlink() or not path.is_file()):
            raise MigrationError('migration inventory is missing, out-of-order or noncanonical')
        with path.open('rb') as stream:
            payload = stream.read(1024 * 1024 + 1)
        total += len(payload)
        if len(payload) > 1024 * 1024 or total > 16 * 1024 * 1024:
            raise MigrationError('migration inventory byte limit exceeded')
        try:
            sql = payload.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
        except UnicodeError:
            raise MigrationError('migration encoding invalid') from None
        if not sql.strip():
            raise MigrationError('empty migration refused')
        result.append(Migration(path.name, sql, hashlib.sha256(sql.encode('utf-8')).hexdigest()))
    return tuple(result)


def validate_history(inventory, rows) -> int:
    """Validate the entire prefix before executing any pending SQL."""
    if len(rows) > 999:
        raise MigrationError('migration history limit exceeded')
    expected = {item.name: item.checksum for item in inventory}
    applied = dict(rows)
    if len(applied) != len(rows):
        raise MigrationError('duplicate migration history')
    if set(applied) - set(expected):
        raise MigrationError('applied migrations are missing from the migration directory')
    if sorted(applied) != [item.name for item in inventory[:len(applied)]]:
        raise MigrationError('migration history is not a contiguous prefix')
    for name, checksum in applied.items():
        if checksum is None:
            raise MigrationError('migration checksum evidence required')
        if checksum != expected[name]:
            raise MigrationError('migration checksum mismatch for ' + name)
    return len(applied)


def apply_migrations(connection: Connection, migrations_dir: str | Path, *, schema: str | None = None) -> list[str]:
    """Transactional reviewed DDL; caller commits. Never repairs unverifiable history.

    PostgreSQL callers must use 16/17 READ COMMITTED and an existing transaction.
    A transaction advisory lock excludes competing runners. All SQL rolls back to
    a savepoint on failure even if the caller catches the exception and commits.
    This function is not deployment permission and has no network/host dispatch.
    """
    inventory = migration_inventory(migrations_dir)
    dialect = connection.dialect.name
    if dialect not in {'sqlite', 'postgresql'}:
        raise MigrationError('unsupported migration database')
    with connection.begin_nested():
        if dialect == 'postgresql':
            major = int(connection.execute(text('SHOW server_version_num')).scalar_one()) // 10000
            isolation = connection.execute(text('SHOW transaction_isolation')).scalar_one()
            if major not in {16, 17} or isolation != 'read committed':
                raise MigrationError('PostgreSQL 16/17 READ COMMITTED required')
            if not connection.execute(text("SELECT pg_catalog.pg_try_advisory_xact_lock(pg_catalog.hashtext('hope:schema-migrations')::bigint)")).scalar_one():
                raise MigrationError('concurrent migration runner refused')
            settings = connection.execute(text("SELECT name,setting::bigint FROM pg_catalog.pg_settings WHERE name IN ('statement_timeout','lock_timeout')")).all()
            for setting, milliseconds in settings:
                ceiling = {'statement_timeout':30000,'lock_timeout':5000}[setting]
                if milliseconds <= 0 or milliseconds > ceiling:
                    connection.execute(text(f"SET LOCAL {setting}='{ceiling}ms'"))
            selected = schema if schema is not None else connection.execute(text('SELECT pg_catalog.current_schema()')).scalar_one()
            if (not isinstance(selected,str) or re.fullmatch('[a-z_][a-z0-9_]{0,62}',selected) is None
                    or selected.startswith('pg_') or selected=='information_schema'):
                raise MigrationError('migration target schema invalid')
            connection.execute(text(f'SET LOCAL search_path="{selected}",pg_catalog,public,pg_temp'))
            table = selected+'.hope_schema_migrations'
            exists = connection.execute(text('SELECT pg_catalog.to_regclass(:table)'),{'table':table}).scalar_one() is not None
        else:
            if schema not in {None,'main'}:raise MigrationError('SQLite migration schema unsupported')
            table = 'hope_schema_migrations'
            exists = connection.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='hope_schema_migrations'")).first() is not None
        if exists:
            try:
                rows = [tuple(row) for row in connection.execute(text(f'SELECT version,checksum FROM {table} ORDER BY version LIMIT 1000'))]
            except Exception:
                raise MigrationError('migration checksum evidence required') from None
        else:
            rows = []
        count = validate_history(inventory, rows)
        if not exists:
            timestamp = 'TEXT' if dialect == 'sqlite' else 'TIMESTAMPTZ'
            connection.execute(text(f"CREATE TABLE {table} (version TEXT PRIMARY KEY, applied_at {timestamp} NOT NULL DEFAULT CURRENT_TIMESTAMP, checksum CHAR(64))"))
        newly_applied = []
        for migration in inventory[count:]:
            connection.exec_driver_sql(migration.sql)
            connection.execute(text(f'INSERT INTO {table}(version,checksum) VALUES (:version,:checksum)'),
                               dict(version=migration.name,checksum=migration.checksum))
            newly_applied.append(migration.name)
        if migration_inventory(migrations_dir) != inventory:
            raise MigrationError('migration source changed during application')
        final = [tuple(row) for row in connection.execute(text(f'SELECT version,checksum FROM {table} ORDER BY version LIMIT 1000'))]
        if validate_history(inventory, final) != len(inventory):
            raise MigrationError('migration postcondition failed')
        return newly_applied
