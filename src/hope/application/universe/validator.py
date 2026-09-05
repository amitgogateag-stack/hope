from __future__ import annotations

from collections.abc import Iterable

from hope.domain.universe.models import UniverseMember, UniverseVersion


class UniverseValidationError(ValueError):
    pass


def validate_members(version: UniverseVersion, members: Iterable[UniverseMember]) -> tuple[UniverseMember, ...]:
    """Validate membership cardinality and identity uniqueness before publication."""
    materialized = tuple(members)
    ids = [member.instrument_id for member in materialized]
    if len(ids) != len(set(ids)):
        raise UniverseValidationError("universe contains duplicate instrument identities")
    if len(materialized) != version.declared_member_count:
        raise UniverseValidationError(
            f"declared member count {version.declared_member_count} != actual {len(materialized)}"
        )
    return materialized
