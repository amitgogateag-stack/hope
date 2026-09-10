from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, Uuid, create_engine

from hope.domain.universe.models import UniverseMember
from hope.infrastructure.repositories.universe_members import UniverseMemberRepository
from hope.infrastructure.repositories.universe_snapshots import UniverseSnapshotRepository
from hope.infrastructure.repositories.universes import UniverseRepository, UniverseVersionRecord


def _create_tables(connection) -> None:
    metadata = MetaData()
    Table(
        "universe_versions",
        metadata,
        Column("universe_version_id", Uuid, primary_key=True),
        Column("universe_id", Uuid, nullable=False),
        Column("version", String, nullable=False),
        Column("pit_certified", Boolean, nullable=False),
        Column("declared_member_count", Integer, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    Table(
        "universe_members",
        metadata,
        Column("universe_version_id", Uuid, primary_key=True),
        Column("instrument_id", Uuid, primary_key=True),
        Column("valid_from", DateTime(timezone=True)),
        Column("valid_to", DateTime(timezone=True)),
    )
    metadata.create_all(connection)


def test_universe_snapshot_repository_loads_exact_durable_snapshot() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        _create_tables(connection)
        version_id = uuid4()
        universe_id = uuid4()
        members = (
            UniverseMember(instrument_id=uuid4(), valid_from=datetime(2026, 1, 1, tzinfo=timezone.utc)),
            UniverseMember(instrument_id=uuid4(), valid_from=datetime(2026, 2, 1, tzinfo=timezone.utc)),
        )
        UniverseRepository(connection).create(
            UniverseVersionRecord(
                universe_version_id=version_id,
                universe_id=universe_id,
                version="2026-02",
                pit_certified=True,
                declared_member_count=2,
            )
        )
        member_repo = UniverseMemberRepository(connection)
        for member in reversed(members):
            member_repo.add(version_id, member)

        snapshot = UniverseSnapshotRepository(connection).get(version_id)

        assert snapshot is not None
        assert snapshot.universe_version_id == version_id
        assert snapshot.version.universe_id == universe_id
        assert snapshot.version.version == "2026-02"
        assert snapshot.version.pit_certified is True
        assert snapshot.version.declared_member_count == 2
        assert {member.instrument_id for member in snapshot.members} == {member.instrument_id for member in members}
        assert len(snapshot.membership_hash) == 64


def test_universe_snapshot_repository_returns_none_for_unknown_version() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        _create_tables(connection)
        assert UniverseSnapshotRepository(connection).get(uuid4()) is None


def test_universe_snapshot_repository_fails_closed_on_member_count_mismatch() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        _create_tables(connection)
        version_id = uuid4()
        UniverseRepository(connection).create(
            UniverseVersionRecord(
                universe_version_id=version_id,
                universe_id=uuid4(),
                version="broken",
                pit_certified=True,
                declared_member_count=1,
            )
        )

        with pytest.raises(ValueError, match="declared member count"):
            UniverseSnapshotRepository(connection).get(version_id)
