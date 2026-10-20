"""Read-only legacy-schema research inventory. Never infers strategy performance.

This intentionally does not produce the dashboard v1 snapshot. Its output is
diagnostic evidence for planning a separate, reviewed database integration.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from sqlalchemy import Connection, create_engine, text

from hope.infrastructure.durable_file import durable_replace

_REQUIRED = {
    "experiments": {"experiment_id", "status", "environment"},
    "strategy_versions": {"strategy_version_id", "strategy_id", "version"},
    "datasets": {"dataset_id", "pit_certified"},
}


def inspect_legacy_research(connection: Connection) -> dict[str, object]:
    """Read only metadata and aggregate counts from the initial HOPE schema."""
    connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    timestamp = connection.execute(text("SELECT CURRENT_TIMESTAMP")).scalar_one()
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
        raise ValueError("HOPE_LEGACY_DATABASE_TIME_INVALID")
    rows = connection.execute(text(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' "
        "AND table_name IN ('experiments','strategy_versions','datasets')"
    )).all()
    present: dict[str, set[str]] = {table: set() for table in _REQUIRED}
    for table, column in rows:
        if table in present and isinstance(column, str):
            present[table].add(column)
    missing = {
        table: sorted(required - present[table])
        for table, required in _REQUIRED.items()
        if required - present[table]
    }
    if missing:
        # Do not query any partially compatible or missing table.
        return {
            "schema_version": 1,
            "source": "hope_legacy_research_inventory",
            "generated_at": timestamp.astimezone(timezone.utc).isoformat(),
            "schema_compatible": False,
            "missing_columns": missing,
            "research": None,
            "paper": None,
        }

    def count(sql: str) -> int:
        value = connection.execute(text(sql)).scalar_one()
        if type(value) is not int or not 0 <= value <= 9007199254740991:
            raise ValueError("HOPE_LEGACY_INVALID_COUNT")
        return value

    return {
        "schema_version": 1,
        "source": "hope_legacy_research_inventory",
        "generated_at": timestamp.astimezone(timezone.utc).isoformat(),
        "schema_compatible": True,
        "missing_columns": {},
        "research": {
            "registered_experiments": count("SELECT count(*) FROM public.experiments"),
            "registered_strategy_versions": count("SELECT count(*) FROM public.strategy_versions"),
            "registered_datasets": count("SELECT count(*) FROM public.datasets"),
        },
        "paper": None,
    }


def export_legacy_research_inventory(output: Path, database_url: str) -> Path:
    """Atomically export verified legacy aggregates; never write incompatible data."""
    if not database_url:
        raise ValueError("HOPE_LEGACY_READONLY_DATABASE_URL_REQUIRED")
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            data = inspect_legacy_research(connection)
            connection.rollback()
    finally:
        engine.dispose()
    if not data["schema_compatible"]:
        raise RuntimeError("HOPE_LEGACY_SCHEMA_INCOMPATIBLE")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=".hope-legacy-", suffix=".json", dir=output.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        durable_replace(temp_name, output)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect legacy HOPE research counts (read-only)")
    parser.add_argument("--database-url-env", default="HOPE_LEGACY_READONLY_DATABASE_URL")
    parser.add_argument("--output", type=Path, help="Atomically export verified aggregate JSON")
    args = parser.parse_args()
    if args.database_url_env != "HOPE_LEGACY_READONLY_DATABASE_URL":
        parser.error("Only the dedicated read-only environment variable is supported")
    database_url = os.environ.get(args.database_url_env, "")
    if not database_url:
        parser.error("HOPE_LEGACY_READONLY_DATABASE_URL is required")
    if args.output is not None:
        export_legacy_research_inventory(args.output, database_url)
    else:
        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                result = inspect_legacy_research(connection)
                connection.rollback()
        finally:
            engine.dispose()
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
