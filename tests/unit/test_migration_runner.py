from pathlib import Path
from sqlalchemy import create_engine, text
from hope.infrastructure.postgres.migrations import apply_migrations


def test_migration_runner_is_idempotent(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "001_one.sql").write_text("CREATE TABLE one (id INTEGER PRIMARY KEY);", encoding="utf-8")
    (migrations / "002_two.sql").write_text("CREATE TABLE two (id INTEGER PRIMARY KEY);", encoding="utf-8")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        assert apply_migrations(connection, migrations) == ["001_one.sql", "002_two.sql"]
        assert apply_migrations(connection, migrations) == []
        assert connection.execute(text("SELECT count(*) FROM hope_schema_migrations")).scalar_one() == 2


def test_migration_runner_rejects_missing_directory(tmp_path: Path) -> None:
    import pytest
    from hope.infrastructure.postgres.migrations import MigrationError
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        with pytest.raises(MigrationError):
            apply_migrations(connection, tmp_path / "missing")


def test_migration_runner_rejects_applied_migration_missing_from_directory(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    first = migrations / "001_one.sql"
    first.write_text("CREATE TABLE one (id INTEGER PRIMARY KEY);", encoding="utf-8")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        apply_migrations(connection, migrations)
        first.unlink()
        from hope.infrastructure.postgres.migrations import MigrationError
        import pytest
        with pytest.raises(MigrationError, match="missing from the migration directory"):
            apply_migrations(connection, migrations)


def test_migration_checksum_changes_are_rejected(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    first = migrations / "001_one.sql"
    first.write_text("CREATE TABLE one (id INTEGER PRIMARY KEY);", encoding="utf-8")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        apply_migrations(connection, migrations)
        first.write_text("CREATE TABLE one (id INTEGER PRIMARY KEY, value TEXT);", encoding="utf-8")
        from hope.infrastructure.postgres.migrations import MigrationError
        import pytest
        with pytest.raises(MigrationError, match="checksum mismatch"):
            apply_migrations(connection, migrations)
