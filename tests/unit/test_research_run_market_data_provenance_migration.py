from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_research_run_market_data_provenance_binds_immutable_manifest_identity() -> None:
    sql = (ROOT / "migrations/079_research_run_market_data_provenance.sql").read_text()

    assert "CREATE TABLE research_run_market_data_provenance" in sql
    assert "research_run_id UUID PRIMARY KEY REFERENCES research_runs(research_run_id)" in sql
    assert "dataset_version_id UUID NOT NULL REFERENCES dataset_versions(dataset_version_id)" in sql
    assert "universe_version_id UUID NOT NULL REFERENCES universe_versions(universe_version_id)" in sql
    assert "manifest_hash CHAR(64) NOT NULL" in sql
    assert "LEFT JOIN market_data_coverage_manifests" in sql
    assert "RESEARCH_RUN_MARKET_DATA_UNIVERSE_MISMATCH" in sql
    assert "AFTER INSERT ON research_runs" in sql
    assert "RESEARCH_RUN_MARKET_DATA_PROVENANCE_IMMUTABLE" in sql
    assert "BEFORE UPDATE OR DELETE ON research_run_market_data_provenance" in sql
