from __future__ import annotations

from uuid import UUID

from sqlalchemy.engine import Connection

from hope.application.universe.snapshot import UniverseSnapshot
from hope.domain.universe.models import UniverseVersion
from hope.infrastructure.repositories.universe_members import UniverseMemberRepository
from hope.infrastructure.repositories.universes import UniverseRepository


class UniverseSnapshotRepository:
    """Authoritative read adapter for one durable universe version and its members."""

    def __init__(self, connection: Connection) -> None:
        self._versions = UniverseRepository(connection)
        self._members = UniverseMemberRepository(connection)

    def get(self, universe_version_id: UUID) -> UniverseSnapshot | None:
        if not isinstance(universe_version_id, UUID):
            raise TypeError("UNIVERSE_SNAPSHOT_REPOSITORY_REQUIRES_VERSION_ID")

        record = self._versions.get(universe_version_id)
        if record is None:
            return None

        version = UniverseVersion(
            universe_id=record.universe_id,
            version=record.version,
            declared_member_count=record.declared_member_count,
            pit_certified=record.pit_certified,
        )
        return UniverseSnapshot(
            universe_version_id=record.universe_version_id,
            version=version,
            members=self._members.list(record.universe_version_id),
        )
