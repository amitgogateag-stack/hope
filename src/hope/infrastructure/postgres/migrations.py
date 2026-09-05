from __future__ import annotations

from pathlib import Path
import hashlib
from sqlalchemy import Connection, text


class MigrationError(RuntimeError):
    pass


def apply_migrations(connection: Connection, migrations_dir: str | Path) -> list[str]:
    """Apply ordered SQL migrations exactly once, recording applied filenames."""
    directory = Path(migrations_dir)
    files = sorted(directory.glob("*.sql"))

    if connection.dialect.name == "sqlite":
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS hope_schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                checksum TEXT
            )
        """))
    else:
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS hope_schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                checksum CHAR(64)
            )
        """))
        connection.execute(text("ALTER TABLE hope_schema_migrations ADD COLUMN IF NOT EXISTS checksum CHAR(64)"))
    rows = connection.execute(text("SELECT version, checksum FROM hope_schema_migrations")).all()
    applied = {row[0]: row[1] for row in rows}
    if not files:
        if applied:
            raise MigrationError(
                "applied migrations are missing from the migration directory: "
                + ", ".join(sorted(applied))
            )
        raise MigrationError(f"no migrations found in {directory}")
    current_versions = {migration.name for migration in files}
    missing_files = sorted(set(applied) - current_versions)
    if missing_files:
        raise MigrationError(
            "applied migrations are missing from the migration directory: "
            + ", ".join(missing_files)
        )
    newly_applied: list[str] = []
    for migration in files:
        sql = migration.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        if migration.name in applied:
            recorded = applied[migration.name]
            if recorded is not None and recorded != checksum:
                raise MigrationError(
                    f"migration checksum mismatch for {migration.name}: "
                    f"recorded={recorded} current={checksum}"
                )
            continue
        connection.exec_driver_sql(sql)
        # Migration 008 introduces the checksum column for pre-existing schemas.
        connection.execute(
            text("INSERT INTO hope_schema_migrations(version, checksum) VALUES (:version, :checksum)"),
            {"version": migration.name, "checksum": checksum},
        )
        newly_applied.append(migration.name)
    return newly_applied
