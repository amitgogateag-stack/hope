from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from hope.application.experiments.config_hash import configuration_hash
from hope.application.universe.validator import validate_members
from hope.domain.universe.models import UniverseMember, UniverseVersion


def _require_timezone_aware(value: datetime | None, *, field_name: str) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"UNIVERSE_SNAPSHOT_{field_name}_MUST_BE_TIMEZONE_AWARE")


@dataclass(frozen=True)
class UniverseSnapshot:
    """Immutable binding between one durable universe version and its exact members."""

    universe_version_id: UUID
    version: UniverseVersion
    members: tuple[UniverseMember, ...]
    membership_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.universe_version_id, UUID):
            raise TypeError("UNIVERSE_SNAPSHOT_REQUIRES_VERSION_ID")
        if not isinstance(self.version, UniverseVersion):
            raise TypeError("UNIVERSE_SNAPSHOT_REQUIRES_VERSION")
        if not isinstance(self.members, tuple):
            raise TypeError("UNIVERSE_SNAPSHOT_MEMBERS_MUST_BE_TUPLE")
        if any(not isinstance(member, UniverseMember) for member in self.members):
            raise TypeError("UNIVERSE_SNAPSHOT_REQUIRES_UNIVERSE_MEMBERS")

        validated = validate_members(self.version, self.members)
        for member in validated:
            _require_timezone_aware(member.valid_from, field_name="VALID_FROM")
            _require_timezone_aware(member.valid_to, field_name="VALID_TO")

        ordered = tuple(sorted(validated, key=lambda member: str(member.instrument_id)))
        object.__setattr__(self, "members", ordered)
        object.__setattr__(
            self,
            "membership_hash",
            configuration_hash(
                [member.model_dump(mode="json") for member in ordered]
            ),
        )
