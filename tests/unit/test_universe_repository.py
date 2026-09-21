from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, Uuid, create_engine
from hope.infrastructure.repositories.universes import UniverseRepository, UniverseVersionRecord


def test_universe_version_round_trip() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        metadata = MetaData()
        Table("universe_versions", metadata,
              Column("universe_version_id", Uuid, primary_key=True),
              Column("universe_id", Uuid, nullable=False), Column("version", String, nullable=False),
              Column("pit_certified", Boolean, nullable=False), Column("declared_member_count", Integer, nullable=False),
              Column("created_at", DateTime(timezone=True), nullable=False))
        metadata.create_all(connection)
        repo = UniverseRepository(connection)
        record = UniverseVersionRecord(universe_version_id=uuid4(), universe_id=uuid4(), version="u1", pit_certified=False, declared_member_count=256)
        repo.create(record)
        stored = repo.get(record.universe_version_id)
        assert stored is not None
        assert stored.model_copy(update={"created_at": None}) == record
        assert stored.created_at is not None


@pytest.mark.parametrize("version", ["", " ", " u1", "u1 "])
def test_universe_version_record_rejects_noncanonical_version(version: str) -> None:
    with pytest.raises(ValueError, match="UNIVERSE_VERSION"):
        UniverseVersionRecord(
            universe_version_id=uuid4(),
            universe_id=uuid4(),
            version=version,
            pit_certified=False,
            declared_member_count=1,
        )


def test_universe_version_record_rejects_naive_created_at() -> None:
    with pytest.raises(ValueError, match="UNIVERSE_VERSION_CREATED_AT_MUST_BE_TIMEZONE_AWARE"):
        UniverseVersionRecord(
            universe_version_id=uuid4(),
            universe_id=uuid4(),
            version="u1",
            pit_certified=False,
            declared_member_count=1,
            created_at=datetime(2026, 1, 1),
        )


def test_universe_repository_rejects_noncanonical_stored_version() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        metadata = MetaData()
        table = Table("universe_versions", metadata,
              Column("universe_version_id", Uuid, primary_key=True),
              Column("universe_id", Uuid, nullable=False), Column("version", String, nullable=False),
              Column("pit_certified", Boolean, nullable=False), Column("declared_member_count", Integer, nullable=False),
              Column("created_at", DateTime(timezone=True), nullable=False))
        metadata.create_all(connection)
        version_id = uuid4()
        connection.execute(table.insert().values(
            universe_version_id=version_id,
            universe_id=uuid4(),
            version=" u1 ",
            pit_certified=False,
            declared_member_count=1,
            created_at=datetime.now(timezone.utc),
        ))
        with pytest.raises(ValueError, match="UNIVERSE_VERSION_NOT_CANONICAL"):
            UniverseRepository(connection).get(version_id)
