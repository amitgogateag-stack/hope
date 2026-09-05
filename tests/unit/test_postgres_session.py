from hope.infrastructure.postgres.session import DEFAULT_DATABASE_URL, database_url, create_db_engine


def test_default_database_url_is_paper_research_database() -> None:
    assert "hope" in DEFAULT_DATABASE_URL
    assert "localhost" in DEFAULT_DATABASE_URL


def test_engine_creation_is_lazy() -> None:
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    assert engine.url.get_backend_name() == "sqlite"
    engine.dispose()
