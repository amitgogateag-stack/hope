from datetime import datetime, timezone, tzinfo
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
    naive = UniverseMember.model_construct(
        instrument_id=UUID("11111111-1111-1111-1111-111111111111"),
        valid_from=datetime(2026, 1, 1),
        valid_to=None,
    )

    with pytest.raises(ValueError, match="UNIVERSE_SNAPSHOT_VALID_FROM_MUST_BE_TIMEZONE_AWARE"):
        UniverseSnapshot(version_id, _version(declared_member_count=1), (naive,))


@pytest.mark.parametrize("version", ["", "   "])
def test_universe_version_identity_must_be_nonblank(version) -> None:
    with pytest.raises(ValueError, match="UNIVERSE_VERSION_REQUIRED"):
        UniverseVersion(
            universe_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            version=version,
            declared_member_count=0,
        )


@pytest.mark.parametrize("version", [" u1", "u1 "])
def test_universe_version_identity_must_be_canonical(version) -> None:
    with pytest.raises(ValueError, match="UNIVERSE_VERSION_NOT_CANONICAL"):
        UniverseVersion(
            universe_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            version=version,
            declared_member_count=0,
        )


class MissingOffsetTimezone(tzinfo):
    def utcoffset(self, dt):
        return None


@pytest.mark.parametrize("field", ["valid_from", "valid_to"])
@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 1, 1),
        datetime(2026, 1, 1, tzinfo=MissingOffsetTimezone()),
    ],
)
def test_universe_member_requires_unambiguous_aware_interval_times(field, value) -> None:
    values = {
        "instrument_id": UUID("11111111-1111-1111-1111-111111111111"),
        field: value,
    }
    with pytest.raises(ValueError, match="UNIVERSE_MEMBER_TIME_MUST_BE_TIMEZONE_AWARE"):
        UniverseMember(**values)


@pytest.mark.parametrize("model", [UniverseVersion, UniverseMember])
def test_universe_models_forbid_undeclared_fields(model) -> None:
    if model is UniverseVersion:
        values = {
            "universe_id": UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            "version": "u1",
            "declared_member_count": 0,
        }
    else:
        values = {
            "instrument_id": UUID("11111111-1111-1111-1111-111111111111"),
        }

    with pytest.raises(ValueError, match="extra_forbidden"):
        model(**values, undeclared="must-fail")


def test_universe_snapshot_hash_canonicalizes_equivalent_timezones_to_utc() -> None:
    version_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    instrument = UUID("11111111-1111-1111-1111-111111111111")
    other = UniverseMember(instrument_id=UUID("22222222-2222-2222-2222-222222222222"))
    utc_member = UniverseMember(
        instrument_id=instrument,
        valid_from=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        valid_to=datetime(2026, 1, 2, 12, 0, tzinfo=UTC),
    )
    offset = timezone.utc.__class__(__import__("datetime").timedelta(hours=5, minutes=30))
    offset_member = UniverseMember(
        instrument_id=instrument,
        valid_from=datetime(2026, 1, 1, 17, 30, tzinfo=offset),
        valid_to=datetime(2026, 1, 2, 17, 30, tzinfo=offset),
    )

    assert UniverseSnapshot(version_id, _version(), (utc_member, other)).membership_hash == UniverseSnapshot(
        version_id, _version(), (offset_member, other)
    ).membership_hash
