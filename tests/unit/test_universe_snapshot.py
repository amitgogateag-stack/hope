from datetime import datetime, timezone
from uuid import UUID

import pytest

from hope.application.universe.snapshot import UniverseSnapshot
from hope.domain.universe.models import UniverseMember, UniverseVersion


UTC = timezone.utc


def _version(*, declared_member_count: int = 2) -> UniverseVersion:
    return UniverseVersion(
        universe_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        version="u1",
        declared_member_count=declared_member_count,
        pit_certified=True,
    )


def test_universe_snapshot_canonicalizes_member_order_and_hash() -> None:
    first = UniverseMember(instrument_id=UUID("11111111-1111-1111-1111-111111111111"))
    second = UniverseMember(instrument_id=UUID("22222222-2222-2222-2222-222222222222"))
    version_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

    forward = UniverseSnapshot(version_id, _version(), (first, second))
    reverse = UniverseSnapshot(version_id, _version(), (second, first))

    assert forward.members == (first, second)
    assert reverse.members == forward.members
    assert reverse.membership_hash == forward.membership_hash
    assert len(forward.membership_hash) == 64


def test_universe_snapshot_hash_changes_when_exact_membership_changes() -> None:
    version_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    first = UniverseMember(instrument_id=UUID("11111111-1111-1111-1111-111111111111"))
    second = UniverseMember(instrument_id=UUID("22222222-2222-2222-2222-222222222222"))
    changed = UniverseMember(instrument_id=UUID("33333333-3333-3333-3333-333333333333"))

    baseline = UniverseSnapshot(version_id, _version(), (first, second))
    modified = UniverseSnapshot(version_id, _version(), (first, changed))

    assert modified.membership_hash != baseline.membership_hash


def test_universe_snapshot_hash_includes_membership_validity_window() -> None:
    version_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    instrument = UUID("11111111-1111-1111-1111-111111111111")
    other = UniverseMember(instrument_id=UUID("22222222-2222-2222-2222-222222222222"))
    early = UniverseMember(
        instrument_id=instrument,
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
    )
    later = UniverseMember(
        instrument_id=instrument,
        valid_from=datetime(2026, 2, 1, tzinfo=UTC),
    )

    assert UniverseSnapshot(version_id, _version(), (early, other)).membership_hash != UniverseSnapshot(
        version_id, _version(), (later, other)
    ).membership_hash


def test_universe_snapshot_rejects_invalid_membership_before_hashing() -> None:
    version_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    member = UniverseMember(instrument_id=UUID("11111111-1111-1111-1111-111111111111"))

    with pytest.raises(ValueError, match="duplicate"):
        UniverseSnapshot(version_id, _version(), (member, member))

    with pytest.raises(ValueError, match="declared member count"):
        UniverseSnapshot(version_id, _version(), (member,))


def test_universe_snapshot_rejects_naive_membership_times() -> None:
    version_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    naive = UniverseMember(
        instrument_id=UUID("11111111-1111-1111-1111-111111111111"),
        valid_from=datetime(2026, 1, 1),
    )

    with pytest.raises(ValueError, match="UNIVERSE_SNAPSHOT_VALID_FROM_MUST_BE_TIMEZONE_AWARE"):
        UniverseSnapshot(version_id, _version(declared_member_count=1), (naive,))
