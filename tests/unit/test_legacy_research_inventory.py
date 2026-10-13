"""Legacy research inspection never confuses registrations with proven edge."""
from datetime import datetime, timezone

import pytest

from hope.api.legacy_research_inventory import inspect_legacy_research, export_legacy_research_inventory


class Result:
    def __init__(self, scalar=None, rows=None):
        self.value = scalar
        self.rows = rows

    def scalar_one(self):
        return self.value

    def all(self):
        return self.rows


class FakeConnection:
    def __init__(self, *, missing=None, counts=None):
        self.statements = []
        self.missing = missing or set()
        self.counts = counts or {"experiments": 7, "strategy_versions": 3, "datasets": 2}

    def rollback(self):
        pass

    def execute(self, query):
        sql = str(query)
        self.statements.append(sql)
        if sql.startswith("SET TRANSACTION"):
            return Result()
        if sql == "SELECT CURRENT_TIMESTAMP":
            return Result(scalar=datetime(2026, 10, 13, tzinfo=timezone.utc))
        if sql.startswith("SELECT table_name, column_name"):
            columns = {
                "experiments": ["experiment_id", "status", "environment"],
                "strategy_versions": ["strategy_version_id", "strategy_id", "version"],
                "datasets": ["dataset_id", "pit_certified"],
            }
            return Result(rows=[
                (table, column)
                for table, fields in columns.items()
                for column in fields if (table, column) not in self.missing
            ])
        for table, value in self.counts.items():
            if sql == f"SELECT count(*) FROM public.{table}":
                return Result(scalar=value)
        raise AssertionError("Unexpected SQL: " + sql)


def test_legacy_inventory_is_repeatable_read_and_never_claims_paper_or_edge():
    connection = FakeConnection()
    result = inspect_legacy_research(connection)
    assert connection.statements[0] == (
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    )
    assert result["schema_compatible"] is True
    assert result["research"] == {
        "registered_experiments": 7,
        "registered_strategy_versions": 3,
        "registered_datasets": 2,
    }
    assert result["paper"] is None
    assert result["source"] != "hope_postgresql"
    assert result["generated_at"] == "2026-10-13T00:00:00+00:00"
    assert not any("orders" in sql or "fills" in sql for sql in connection.statements)


@pytest.mark.parametrize("missing", [
    {("experiments", "experiment_id")},
    {("strategy_versions", "strategy_version_id")},
    {("datasets", "pit_certified")},
    {("experiments", "status"), ("datasets", "dataset_id")},
])
def test_missing_columns_stop_all_count_queries(missing):
    connection = FakeConnection(missing=missing)
    result = inspect_legacy_research(connection)
    assert result["schema_compatible"] is False
    assert result["research"] is None
    assert result["paper"] is None
    assert result["missing_columns"]
    assert not any("SELECT count" in sql for sql in connection.statements)


@pytest.mark.parametrize("counts", [
    {"experiments": -1, "strategy_versions": 3, "datasets": 2},
    {"experiments": True, "strategy_versions": 3, "datasets": 2},
    {"experiments": 7, "strategy_versions": 1.5, "datasets": 2},
])
def test_invalid_counts_fail_closed(counts):
    with pytest.raises(ValueError, match="HOPE_LEGACY_INVALID_COUNT"):
        inspect_legacy_research(FakeConnection(counts=counts))


def test_inventory_does_not_mutate_data_or_return_sensitive_records():
    connection = FakeConnection()
    result = inspect_legacy_research(connection)
    assert all(sql.startswith(("SET TRANSACTION", "SELECT")) for sql in connection.statements)
    assert "credentials" not in str(result).lower()
    assert "experiment_id" not in result["research"]
    assert "performance" not in result["research"]


def test_export_writes_only_complete_verified_inventory(tmp_path, monkeypatch):
    import json
    import hope.api.legacy_research_inventory as module

    class Engine:
        def connect(self):
            class Context:
                def __enter__(self):
                    return FakeConnection()
                def __exit__(self, *args):
                    pass
            return Context()
        def dispose(self):
            pass

    monkeypatch.setattr(module, "create_engine", lambda url: Engine())
    output = tmp_path / "legacy-research.json"
    assert export_legacy_research_inventory(output, "postgresql://read-only") == output
    data = json.loads(output.read_text())
    assert data["source"] == "hope_legacy_research_inventory"
    assert data["research"]["registered_experiments"] == 7
    assert data["paper"] is None


def test_export_refuses_missing_url_without_creating_file(tmp_path):
    output = tmp_path / "legacy.json"
    with pytest.raises(ValueError, match="HOPE_LEGACY_READONLY_DATABASE_URL_REQUIRED"):
        export_legacy_research_inventory(output, "")
    assert not output.exists()


def test_export_incompatible_schema_never_overwrites_previous_file(tmp_path, monkeypatch):
    import hope.api.legacy_research_inventory as module
    class Engine:
        def connect(self):
            class Context:
                def __enter__(self):
                    return FakeConnection(missing={("experiments", "experiment_id")})
                def __exit__(self, *args):
                    pass
            return Context()
        def dispose(self):
            pass
    monkeypatch.setattr(module, "create_engine", lambda url: Engine())
    output = tmp_path / "legacy.json"
    output.write_text("prior-valid-snapshot")
    with pytest.raises(RuntimeError, match="HOPE_LEGACY_SCHEMA_INCOMPATIBLE"):
        export_legacy_research_inventory(output, "postgresql://read-only")
    assert output.read_text() == "prior-valid-snapshot"
