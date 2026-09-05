from uuid import uuid4

import pytest

from hope.application.universe.validator import UniverseValidationError, validate_members
from hope.domain.universe.models import UniverseMember, UniverseVersion


def test_universe_cardinality_and_identity_are_validated() -> None:
    version = UniverseVersion(universe_id=uuid4(), version="u1", declared_member_count=2)
    members = [UniverseMember(instrument_id=uuid4()), UniverseMember(instrument_id=uuid4())]
    assert len(validate_members(version, members)) == 2


def test_universe_rejects_duplicate_identity() -> None:
    instrument = uuid4()
    version = UniverseVersion(universe_id=uuid4(), version="u1", declared_member_count=2)
    members = [UniverseMember(instrument_id=instrument), UniverseMember(instrument_id=instrument)]
    with pytest.raises(UniverseValidationError, match="duplicate"):
        validate_members(version, members)


def test_universe_rejects_wrong_declared_count() -> None:
    version = UniverseVersion(universe_id=uuid4(), version="u1", declared_member_count=2)
    with pytest.raises(UniverseValidationError, match="declared member count"):
        validate_members(version, [UniverseMember(instrument_id=uuid4())])
